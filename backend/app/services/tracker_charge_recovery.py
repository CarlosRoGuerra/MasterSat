"""Recupera as avulsas do antigo formulário do rastreador para o fechamento."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.timezone import BRASILIA_TZ
from app.models.billing import Billing
from app.models.billing_adjustment import BillingAdjustment
from app.models.billing_change_log import BillingChangeLog
from app.models.billing_charge_item import BillingChargeItem
from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.closure_job import ClosureJob
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.models.nfse_nota import NfseNota
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.services import titulo_bancario
from app.services.billing_closure.shared import _lock_competencia
from app.services.financial import (
    add_months, billing_ids_for_charge_item, contract_payer_client_id,
    effective_charge_item_billings, lock_billings_for_update, lock_charge_items_for_update,
)

RECOVERY_FIELD = 'fechamento_adicional_rastreador'


def _validate_source(db: Session, billing: Billing) -> None:
    def refuse(reason: str) -> None:
        raise ValueError(f'Cobrança #{billing.id}: {reason}')

    if billing.is_deleted or billing.status not in (BillingStatus.PENDING, BillingStatus.OVERDUE):
        refuse('somente cobranças em aberto podem ser recuperadas.')
    if billing.billing_type != 'avulsa' or not billing.tracker_id or not billing.vehicle_id:
        refuse('não é uma avulsa vinculada a rastreador e veículo.')
    if billing.item_id or billing.substituted_by_id or billing.sgr_payload is not None or billing.somente_sistema:
        refuse('já tem vínculo financeiro ou origem diferente do formulário do rastreador.')
    if billing.payment_date or billing.paid_amount or billing.receipt_number:
        refuse('possui recebimento registrado.')
    if (billing.installment_total or 1) != 1 or (billing.installment_number or 1) != 1:
        refuse('cobrança parcelada não pode ser convertida por esta recuperação.')
    if Decimal(str(billing.amount)) <= 0:
        refuse('valor inválido.')
    if db.scalar(select(Billing.id).where(Billing.substituted_by_id == billing.id).limit(1)):
        refuse('substitui outras cobranças.')
    if db.scalar(select(BillingChargeItem.id).where(BillingChargeItem.billing_id == billing.id).limit(1)):
        refuse('já está associada a serviços.')
    if db.scalar(select(BillingAdjustment.id).where(or_(
        BillingAdjustment.billing_id == billing.id,
        BillingAdjustment.target_billing_id == billing.id,
    )).limit(1)):
        refuse('possui ajustes financeiros.')
    if db.scalar(select(NfseNota.id).where(NfseNota.billing_id == billing.id).limit(1)):
        refuse('possui processo de NFS-e; confira a situação fiscal antes de recuperar.')

    client = db.get(Client, billing.client_id)
    vehicle = db.get(Vehicle, billing.vehicle_id)
    tracker = db.get(Tracker, billing.tracker_id)
    if not client or client.is_deleted:
        refuse('cliente indisponível.')
    if not vehicle or vehicle.is_deleted or vehicle.client_id != billing.client_id:
        refuse('o vínculo do veículo com o cliente mudou.')
    if not tracker or tracker.is_deleted or tracker.client_id != billing.client_id or tracker.vehicle_id != billing.vehicle_id:
        refuse('o vínculo do rastreador com o cliente ou veículo mudou.')
    payer_id = billing.client_id
    if billing.contract_id:
        contract = db.get(Contract, billing.contract_id)
        if not contract or contract.is_deleted or contract.client_id != billing.client_id:
            refuse('contrato indisponível ou de outro cliente.')
        if contract.vehicle_id != billing.vehicle_id or contract.tracker_id != billing.tracker_id:
            refuse('o vínculo do contrato com o rastreador ou veículo mudou.')
        payer_id = contract_payer_client_id(db, contract)
    if (billing.payer_client_id or billing.client_id) != payer_id:
        refuse('o responsável financeiro mudou; reconcilie o pagador antes de recuperar.')


def recover_tracker_charges(
    db: Session, billing_ids: list[int], service_month: date, *, apply: bool = False,
    expected_client_id: int,
) -> list[dict]:
    """Valida o lote inteiro antes de escrever. O chamador confirma ou desfaz.

    A origem fica cancelada, vinculada ao novo item e com trilha de auditoria.
    Não há emissão bancária nem geração de mensalidades nesta operação.
    """
    if not billing_ids or any(bid <= 0 for bid in billing_ids) or service_month.day != 1:
        raise ValueError('Informe cobranças válidas e o primeiro dia do mês do serviço.')
    # Mesma trava do fechamento que receberá o item (vencimento no mês seguinte).
    _lock_competencia(db, add_months(service_month, 1))
    billings = lock_billings_for_update(db, billing_ids)
    if {b.id for b in billings} != set(billing_ids):
        raise ValueError('Uma das cobranças informadas não foi encontrada.')
    month_label = service_month.strftime('%Y-%m')
    markers = {
        log.billing_id: log for log in db.scalars(select(BillingChangeLog).where(
            BillingChangeLog.billing_id.in_(billing_ids),
            BillingChangeLog.field_name == RECOVERY_FIELD,
        ).order_by(BillingChangeLog.id)).all()
    }
    pending = []
    entries = []
    for billing in billings:
        if billing.client_id != expected_client_id:
            raise ValueError(f'Cobrança #{billing.id} não pertence ao cliente esperado.')
        marker = markers.get(billing.id)
        if marker:
            previous = json.loads(marker.new_value or '{}')
            if previous.get('service_month') != month_label:
                raise ValueError(f'Cobrança #{billing.id} já recuperada para outro mês.')
            item = db.get(ClientChargeItem, previous.get('item_id'))
            if not item or billing.item_id != item.id or billing.status != BillingStatus.CANCELED:
                raise ValueError(f'Cobrança #{billing.id}: vínculo da recuperação anterior foi alterado.')
            item_month = item.start_date.replace(day=1)
            if item.date_basis == 'due_date':
                item_month = add_months(item_month, -1)
            if item_month != service_month:
                raise ValueError(f'Cobrança #{billing.id}: o mês do lançamento recuperado foi alterado.')
        else:
            _validate_source(db, billing)
            if billing.due_date.replace(day=1) != add_months(service_month, 1):
                raise ValueError(f'Cobrança #{billing.id}: o vencimento não pertence ao mês seguinte ao fechamento informado.')
            pending.append(billing)
            item = None
        entries.append({
            'billing_id': billing.id, 'client_id': billing.client_id,
            'title': billing.title, 'amount': str(billing.amount),
            'original_due_date': billing.due_date.isoformat(),
            'service_month': month_label, 'item_id': item.id if item else None,
            'result': 'ja_recuperada' if item else 'pronta_para_recuperar',
        })
    pending_ids = {billing.id for billing in pending}
    for result in db.scalars(select(ClosureJob.result)).all():
        if pending_ids.intersection((result or {}).get('payment_billing_ids', [])):
            raise ValueError('Uma das cobranças já pertence a um fechamento.')
    # Sob a trava do Billing: concorre corretamente com emissão e recebimento.
    titulo_bancario.exigir(db, titulo_bancario.ALTERAR_VALOR, pending_ids)
    if not apply:
        return entries

    entries_by_id = {entry['billing_id']: entry for entry in entries}
    for billing in pending:
        title = (billing.title or 'Cobrança manual do rastreador').strip()
        description = '\n'.join(filter(None, [
            billing.notes, f'Origem: cobrança avulsa #{billing.id}. Título original: {title}.',
            f'Vencimento original: {billing.due_date.isoformat()}. Mês do serviço: {month_label}.',
        ]))
        item = ClientChargeItem(
            client_id=billing.client_id, contract_id=billing.contract_id,
            vehicle_id=billing.vehicle_id, tracker_id=billing.tracker_id,
            title=title[:140], description=description, quantity=1,
            unit_price=billing.amount, total_amount=billing.amount, installment_count=1,
            start_date=billing.due_date, date_basis='due_date',
            active=True, status='ativo',
        )
        db.add(item)
        db.flush()
        previous_status = billing.status.value
        billing.status = BillingStatus.CANCELED
        billing.item_id = item.id
        justification = f'Recuperação da cobrança do rastreador para o fechamento de {month_label}.'
        billing.notes = '\n'.join(filter(None, [billing.notes, f'{justification} Lançamento #{item.id}.']))
        db.add_all([
            BillingChangeLog(
                billing_id=billing.id, field_name='status', previous_value=previous_status,
                new_value=BillingStatus.CANCELED.value, justification=justification,
            ),
            BillingChangeLog(
                billing_id=billing.id, field_name=RECOVERY_FIELD, previous_value=None,
                new_value=json.dumps({'service_month': month_label, 'item_id': item.id}),
                justification=justification,
            ),
        ])
        entries_by_id[billing.id].update(item_id=item.id, result='recuperada')
    db.flush()
    return entries


def correct_tracker_charge_due_date(
    db: Session, *, client_id: int, service_month: date,
    due_date: date, amount: Decimal, apply: bool = False,
) -> dict:
    """Corrige um adicional pendente cuja data foi interpretada como serviço.

    Cliente, data e valor devem identificar um único lançamento manual. Mantém
    a data original e muda apenas sua interpretação, com auditoria.
    """
    if due_date.replace(day=1) != add_months(service_month, 1):
        raise ValueError('O vencimento deve estar no mês seguinte ao fechamento informado.')
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal('0.01')):
        raise ValueError('Informe um valor positivo com até duas casas decimais.')
    _lock_competencia(db, add_months(service_month, 1))
    ids = list(db.scalars(select(ClientChargeItem.id).where(
        ClientChargeItem.client_id == client_id, ClientChargeItem.is_deleted.is_(False),
        ClientChargeItem.service_product_id.is_(None),
        ClientChargeItem.tracker_id.is_not(None), ClientChargeItem.vehicle_id.is_not(None),
        ClientChargeItem.installment_count == 1, ClientChargeItem.quantity == 1,
        ClientChargeItem.start_date == due_date, ClientChargeItem.total_amount == amount,
    )).all())
    if len(ids) != 1:
        raise ValueError('Cliente, vencimento e valor não identificam um único adicional do rastreador.')
    item_id = ids[0]
    billing_ids = billing_ids_for_charge_item(db, item_id)
    lock_billings_for_update(db, billing_ids)
    [item] = lock_charge_items_for_update(db, [item_id])
    if (item.is_deleted or item.client_id != client_id or item.start_date != due_date
            or item.total_amount != amount or item.service_product_id is not None
            or not item.tracker_id or not item.vehicle_id
            or item.installment_count != 1 or item.quantity != 1):
        raise ValueError('O lançamento mudou durante a conferência; nenhuma alteração aplicada.')
    already_correct = item.date_basis == 'due_date'
    result = {'item_id': item.id, 'client_id': client_id, 'title': item.title,
        'amount': str(item.total_amount), 'due_date': due_date.isoformat(),
        'service_month': service_month.strftime('%Y-%m'),
        'result': 'ja_corrigido' if already_correct else 'pronto_para_corrigir'}
    if already_correct:
        return result
    if not item.active or effective_charge_item_billings(db, item_id):
        raise ValueError('O adicional já foi faturado ou está inativo; a correção exige um lançamento pendente.')
    titulo_bancario.exigir(db, titulo_bancario.ALTERAR_VALOR, billing_ids)
    if billing_ids and db.scalar(select(NfseNota.id).where(NfseNota.billing_id.in_(billing_ids)).limit(1)):
        raise ValueError('Há processo fiscal vinculado ao lançamento; nenhuma alteração aplicada.')
    if apply:
        item.date_basis = 'due_date'
        db.add(AuditLog(method='SCRIPT', path='/scripts/recuperar_cobrancas_rastreador',
            entity_type='client_charge_item', entity_id=item.id, status_code=200,
            description=json.dumps({'action': 'corrigir_vencimento_adicional',
                'previous_date_basis': 'service_date', 'date_basis': 'due_date',
                'due_date': due_date.isoformat(), 'service_month': service_month.isoformat(),
                'amount': str(amount)}, ensure_ascii=False)))
        db.flush()
        result['result'] = 'corrigido'
    return result


def find_tracker_charge_candidates(db: Session, client_id: int, service_month: date) -> list[int]:
    """Busca as avulsas com os vínculos do formulário antigo, criadas no mês.

    A consulta por mês limita a recuperação automática. IDs explícitos permitem
    corrigir uma competência diferente da data de cadastro sem ampliar a busca.
    """
    start = datetime.combine(service_month, datetime.min.time(), tzinfo=BRASILIA_TZ).astimezone(timezone.utc)
    end = datetime.combine(add_months(service_month, 1), datetime.min.time(), tzinfo=BRASILIA_TZ).astimezone(timezone.utc)
    recovered = select(BillingChangeLog.billing_id).where(BillingChangeLog.field_name == RECOVERY_FIELD)
    return list(db.scalars(select(Billing.id).where(
        Billing.client_id == client_id, Billing.is_deleted.is_(False),
        Billing.billing_type == 'avulsa', Billing.tracker_id.is_not(None), Billing.vehicle_id.is_not(None),
        Billing.created_at >= start, Billing.created_at < end,
        or_(Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]), Billing.id.in_(recovered)),
    ).order_by(Billing.id)).all())
