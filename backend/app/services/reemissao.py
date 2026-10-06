"""Corrigir o vencimento de uma cobrança cujo boleto já foi registrado na Ailos.

A API de Cobrança da Ailos não tem instrução de alteração de vencimento nem de
baixa, e o sistema guarda um único boleto por cobrança. Trocar a data "por
cima" deixaria o boleto antigo pagável sem registro aqui: um pagamento dele não
seria conciliado. Por isso a correção é uma substituição, numa transação só:

* a cobrança atual é cancelada e o boleto dela vai para a lista de baixa
  pendente (baixa manual no internet banking — a API não faz);
* uma cobrança nova, com a data corrigida e o mesmo valor, assume a dívida e
  fica SEM boleto: a emissão é o botão "Gerar boleto (Ailos)" de sempre.

A NFS-e já emitida acompanha a dívida: ela documenta o serviço (tomador,
valor, período), não o boleto — emitir outra para a cobrança nova duplicaria a
nota e o imposto. Emissão em andamento ou de resultado incerto bloqueia.

Boleto único (ou negociação): as cobranças que ele agrupava passam a apontar
para a nova; a antiga sai sem substituto, para que reverter a nova não reabra
o título que já foi para o banco. Cobrança simples (mensalidade, parcela de
serviço): a antiga fica substituída pela nova e continua ocupando o mês/a
parcela — o fechamento não recobra; a nova é um título de pagamento como o
boleto único, com o mês do SERVIÇO (a mensalidade carrega o mês técnico do
vencimento, que só existe para o índice de competência).

Se a antiga pertence a um lote de fechamento, a nova toma o lugar dela no lote:
aparece no mesmo envio por e-mail e a recuperação de lotes não cria um lote
avulso para ela.
"""
from __future__ import annotations

from datetime import date

from app.core.timezone import hoje
from app.models.billing import CONSOLIDATED_BILLING_TYPE, Billing
from app.models.billing_change_log import BillingChangeLog
from app.models.closure_job import ClosureJob
from app.models.enums import BillingStatus
from app.models.nfse_nota import NfseNota
from app.services import titulo_bancario
from app.services.financial import (
    lock_billings_for_update,
    lock_charge_items_for_billings,
    mark_billings_substituted,
    refresh_charge_items_for_billing,
    substituted_originals,
    transfer_charge_items_to_billing,
)


class CorrecaoVencimentoError(ValueError):
    pass


def _lote_do_fechamento(db, billing_id: int) -> ClosureJob | None:
    for job in db.query(ClosureJob).filter(ClosureJob.status == 'completed').with_for_update().all():
        if billing_id in [int(b) for b in (job.result or {}).get('payment_billing_ids') or []]:
            return job
    return None


def _rotulo_do_servico(lote: ClosureJob | None) -> str | None:
    """'2026-09' do lote -> '09/2026' (formato do period_label)."""
    if not lote or not lote.reference_month or len(lote.reference_month) != 7:
        return None
    ano, mes = lote.reference_month.split('-')
    return f'{mes}/{ano}'


def corrigir_vencimento(
    db,
    billing_id: int,
    *,
    due_date: date,
    reason: str,
    user_id: int | None,
    confirmado: bool,
) -> Billing:
    """Devolve a cobrança nova (sem boleto). Não faz commit."""
    reason = (reason or '').strip()
    if len(reason) < 3:
        raise CorrecaoVencimentoError('Informe a justificativa da correção.')
    locked = lock_billings_for_update(db, [billing_id])
    if not locked or locked[0].is_deleted:
        raise CorrecaoVencimentoError('Cobrança não encontrada.')
    antiga = locked[0]
    if antiga.status not in (BillingStatus.PENDING, BillingStatus.OVERDUE):
        raise CorrecaoVencimentoError('Só cobrança pendente ou vencida tem o vencimento corrigido.')
    if due_date == antiga.due_date:
        raise CorrecaoVencimentoError('A data informada é igual ao vencimento atual.')

    # Mesma política do cancelamento: registro em andamento ou desfecho
    # desconhecido bloqueiam; título ativo no banco exige confirmação.
    titulo = titulo_bancario.exigir(
        db, titulo_bancario.CANCELAR, [antiga.id], confirmado=confirmado,
    )[antiga.id]

    nota = db.query(NfseNota).filter(NfseNota.billing_id == antiga.id).with_for_update().first()
    if nota and nota.status in ('processing', 'desconhecido'):
        raise CorrecaoVencimentoError(
            'A NFS-e desta cobrança está em emissão ou com resultado incerto. '
            'Consulte o resultado da nota antes de corrigir o vencimento.'
        )

    lote = _lote_do_fechamento(db, antiga.id)
    agrupadas = substituted_originals(db, antiga.id)
    if agrupadas:
        agrupadas = lock_billings_for_update(db, [b.id for b in agrupadas])
    lock_charge_items_for_billings(db, [antiga, *agrupadas])

    data_br = due_date.strftime('%d/%m/%Y')
    nova = Billing(
        client_id=antiga.client_id,
        payer_client_id=antiga.payer_client_id,
        contract_id=antiga.contract_id,
        vehicle_id=antiga.vehicle_id,
        tracker_id=antiga.tracker_id,
        # Mensalidade/parcela não pode ser copiada com o mesmo tipo: a antiga
        # continua ocupando o mês/a parcela (índices de competência e de
        # parcela). A nova é o título de pagamento, como o boleto único.
        billing_type=antiga.billing_type if agrupadas else CONSOLIDATED_BILLING_TYPE,
        title=antiga.title,
        amount=antiga.amount,
        due_date=due_date,
        status=BillingStatus.PENDING if due_date >= hoje() else BillingStatus.OVERDUE,
        period_label=antiga.period_label if agrupadas else (_rotulo_do_servico(lote) or antiga.period_label),
        notes=f'Reemissão da cobrança #{antiga.id} com vencimento corrigido para {data_br}: {reason}',
    )
    db.add(nova)
    db.flush()

    transfer_charge_items_to_billing(db, [antiga], nova)
    if nota:
        nota.billing_id = nova.id
    if lote:
        ids = [int(b) for b in lote.result['payment_billing_ids']]
        lote.result = {**lote.result, 'payment_billing_ids': [nova.id if b == antiga.id else b for b in ids]}
    marcador = f'Vencimento corrigido: reemitida como cobrança #{nova.id} ({data_br}).'
    if agrupadas:
        for original in agrupadas:
            original.substituted_by_id = nova.id
            original.notes = f'{original.notes} | {marcador}' if original.notes else marcador
        antiga.status = BillingStatus.CANCELED
        antiga.notes = f'{antiga.notes} | {marcador}' if antiga.notes else marcador
    else:
        mark_billings_substituted([antiga], nova, marcador)
    if titulo.ativo_no_banco:
        antiga.notes += (
            f'\n[ATENÇÃO] Boleto Ailos (nosso número {titulo.nosso_numero or "—"}) '
            'segue ativo no banco — baixa manual pendente.'
        )
    titulo_bancario.marcar_baixa_pendente(db, [antiga.id], f'vencimento corrigido (#{nova.id}): {reason}')
    db.add(BillingChangeLog(
        billing_id=antiga.id,
        changed_by_user_id=user_id,
        field_name='due_date',
        previous_value=antiga.due_date.isoformat(),
        new_value=f'{due_date.isoformat()} (cobrança #{nova.id})',
        justification=reason,
    ))
    db.flush()
    refresh_charge_items_for_billing(db, antiga, commit=False)
    refresh_charge_items_for_billing(db, nova, commit=False)
    return nova
