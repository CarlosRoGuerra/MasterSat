from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO, StringIO

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session, aliased

from app.api.deps import require_roles
from app.core.timezone import hoje
from app.db.session import get_db
from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.billing_change_log import BillingChangeLog
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus, UserRole
from app.models.plan import Plan
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.models.user import User
from app.services.ailos_boletos import resolver_pagador
from app.schemas.billing import (
    BillingBatchMaintIn,
    BillingBatchStatusIn,
    BillingCancel,
    BillingChangeLogOut,
    BillingCreate,
    BillingOut,
    BillingReceive,
    BillingUnify,
    BillingUpdate,
    DelinquentClientItem,
    FinancialSummary,
    RevenueReportItem,
)
from app.services.financial import (
    add_months,
    charge_item_payer_client_id,
    decimal_to_float,
    generate_receipt_number,
    lock_charge_items_for_billings,
    lock_billings_for_update,
    marcar_billing_pago,
    normalize_due_date,
    period_bucket,
    plan_title,
    refresh_overdue_statuses,
    refresh_charge_items_for_billing,
    transfer_charge_items_to_billing,
    valor_com_juros,
    contract_payer_client_id,
)

router = APIRouter()

_AILOS_REGISTRATION_IN_PROGRESS = ('REGISTRANDO', 'PROCESSANDO')


def _reject_registered_ailos_billings(db: Session, billing_ids: list[int]) -> None:
    registered = (
        db.query(AilosBoleto.billing_id)
        .filter(
            AilosBoleto.billing_id.in_(billing_ids),
            or_(
                and_(
                    AilosBoleto.linha_digitavel.isnot(None),
                    AilosBoleto.codigo_barras.isnot(None),
                ),
                AilosBoleto.status_ailos.in_(_AILOS_REGISTRATION_IN_PROGRESS),
            ),
        )
        .order_by(AilosBoleto.billing_id.asc())
        .all()
    )
    registered_ids = [row[0] for row in registered]
    if registered_ids:
        raise HTTPException(
            status_code=409,
            detail={
                'code': 'boleto_ailos_registrado',
                'billing_ids': registered_ids,
                'message': (
                    'Cobranças com boleto registrado ou em registro na Ailos não podem ter valor ou '
                    'vencimento alterados nem ser unificadas. Faça a baixa e a reemissão '
                    'pelo fluxo bancário apropriado.'
                ),
            },
        )


def _reject_inflight_ailos_billings(db: Session, billing_ids: list[int]) -> None:
    inflight = [
        row[0]
        for row in (
            db.query(AilosBoleto.billing_id)
            .filter(
                AilosBoleto.billing_id.in_(billing_ids),
                AilosBoleto.status_ailos.in_(_AILOS_REGISTRATION_IN_PROGRESS),
            )
            .order_by(AilosBoleto.billing_id.asc())
            .all()
        )
    ]
    if inflight:
        raise HTTPException(
            status_code=409,
            detail={
                'code': 'boleto_ailos_em_registro',
                'billing_ids': inflight,
                'message': (
                    'Aguarde a conclusão do registro Ailos antes de receber, '
                    'cancelar ou remover estas cobranças.'
                ),
            },
        )


def _lock_billing_or_404(db: Session, billing_id: int) -> Billing:
    locked = lock_billings_for_update(db, [billing_id])
    if not locked or locked[0].is_deleted:
        raise HTTPException(status_code=404, detail='Cobrança não encontrada')
    return locked[0]


def _period_label_sort_key(label: str) -> tuple:
    """period_label costuma ser 'MM/YYYY' — ordenar como string compara o mês
    antes do ano e inverte a faixa em qualquer virada de ano (ex.: '02/2026'
    vem antes de '11/2025' alfabeticamente). Rótulos fora desse formato
    (malformados, ou de outra granularidade) vão pro fim, ordenados por texto."""
    try:
        return (0, datetime.strptime(label, '%m/%Y'))
    except ValueError:
        return (1, label)


def base_query(db: Session, *, refresh_statuses: bool = True):
    if refresh_statuses:
        refresh_overdue_statuses(db)
    # boleto_ailos: título registrado na Ailos (linha digitável + código de
    # barras devolvidos por ela). Vem no JOIN para a tela não precisar de uma
    # consulta por linha só para decidir se mostra o botão de download.
    boleto_ailos = case(
        (and_(AilosBoleto.linha_digitavel.isnot(None),
              AilosBoleto.codigo_barras.isnot(None)), True),
        else_=False,
    ).label('boleto_ailos')
    Payer = aliased(Client)
    return (
        db.query(
            Billing,
            Client.name.label('client_name'),
            Payer.name.label('payer_name'),
            Plan.name.label('plan_name'),
            Contract.status.label('contract_status'),
            Vehicle.plate.label('vehicle_plate'),
            Tracker.imei.label('tracker_identifier'),
            boleto_ailos,
        )
        .join(Client, Client.id == Billing.client_id)
        .join(Payer, Payer.id == func.coalesce(Billing.payer_client_id, Billing.client_id))
        .outerjoin(Contract, Contract.id == Billing.contract_id)
        .outerjoin(Plan, Plan.id == Contract.plan_id)
        .outerjoin(Vehicle, Vehicle.id == Billing.vehicle_id)
        .outerjoin(Tracker, Tracker.id == Billing.tracker_id)
        .outerjoin(AilosBoleto, AilosBoleto.billing_id == Billing.id)
        .filter(
            Billing.is_deleted.is_(False),
            Client.is_deleted.is_(False),
            Payer.is_deleted.is_(False),
            or_(Contract.id.is_(None), Contract.is_deleted.is_(False)),
            or_(Plan.id.is_(None), Plan.is_deleted.is_(False)),
            or_(Vehicle.id.is_(None), Vehicle.is_deleted.is_(False)),
            or_(Tracker.id.is_(None), Tracker.is_deleted.is_(False)),
        )
    )


def serialize_billing(row) -> BillingOut:
    (
        billing, client_name, payer_name, plan_name, contract_status,
        vehicle_plate, tracker_identifier, boleto_ailos,
    ) = row
    overdue_days = 0
    if billing.status == BillingStatus.OVERDUE:
        overdue_days = max((date.today() - billing.due_date).days, 0)
    return BillingOut(
        id=billing.id,
        contract_id=billing.contract_id,
        client_id=billing.client_id,
        payer_client_id=billing.payer_client_id or billing.client_id,
        item_id=billing.item_id,
        vehicle_id=billing.vehicle_id,
        title=billing.title,
        billing_type=billing.billing_type,
        installment_number=billing.installment_number,
        installment_total=billing.installment_total,
        amount=decimal_to_float(billing.amount),
        due_date=billing.due_date,
        status=billing.status,
        payment_date=billing.payment_date,
        payment_method=billing.payment_method,
        notes=billing.notes,
        paid_amount=decimal_to_float(billing.paid_amount) if billing.paid_amount is not None else None,
        receipt_number=billing.receipt_number,
        period_label=billing.period_label,
        client_name=client_name,
        payer_name=payer_name,
        vehicle_plate=vehicle_plate,
        tracker_identifier=tracker_identifier,
        plan_name=plan_name,
        contract_status=contract_status,
        overdue_days=overdue_days,
        valor_com_juros=(
            valor_com_juros(billing.amount, billing.due_date)
            if billing.status == BillingStatus.OVERDUE else None
        ),
        boleto_ailos=bool(boleto_ailos),
    )


def apply_filters(query, search: str | None, status: str | None, client_id: int | None, contract_id: int | None, due_from: date | None, due_to: date | None, vehicle_id: int | None = None):
    if search:
        termo = search.strip()
        condicoes = [
            Client.name.ilike(f'%{termo}%'),
            Client.cpf_cnpj.ilike(f'%{termo}%'),
            Billing.receipt_number.ilike(f'%{termo}%'),
            Billing.notes.ilike(f'%{termo}%'),
            Billing.title.ilike(f'%{termo}%'),
        ]
        # Número da cobrança (o que aparece nas telas como "número do boleto") —
        # busca exata, só quando o termo é puramente numérico.
        if termo.isdigit():
            condicoes.append(Billing.id == int(termo))
        query = query.filter(or_(*condicoes))
    if status:
        query = query.filter(Billing.status == status)
    if client_id:
        query = query.filter(Billing.client_id == client_id)
    if contract_id:
        query = query.filter(Billing.contract_id == contract_id)
    if vehicle_id:
        query = query.filter(Billing.vehicle_id == vehicle_id)
    if due_from:
        query = query.filter(Billing.due_date >= due_from)
    if due_to:
        query = query.filter(Billing.due_date <= due_to)
    return query


@router.get('/summary', response_model=FinancialSummary)
def financial_summary(db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    refresh_overdue_statuses(db)
    active_plans = db.query(func.count(Plan.id)).filter(Plan.is_deleted == False, Plan.active == True).scalar() or 0
    active_contracts = db.query(func.count(Contract.id)).filter(Contract.is_deleted == False, Contract.status == 'ativo').scalar() or 0
    pending_billings = db.query(func.count(Billing.id)).filter(Billing.is_deleted == False, Billing.status == BillingStatus.PENDING).scalar() or 0
    overdue_billings = db.query(func.count(Billing.id)).filter(Billing.is_deleted == False, Billing.status == BillingStatus.OVERDUE).scalar() or 0
    pending_amount = decimal_to_float(db.query(func.coalesce(func.sum(Billing.amount), 0)).filter(Billing.is_deleted == False, Billing.status == BillingStatus.PENDING).scalar())
    overdue_amount = decimal_to_float(db.query(func.coalesce(func.sum(Billing.amount), 0)).filter(Billing.is_deleted == False, Billing.status == BillingStatus.OVERDUE).scalar())
    now = date.today()
    paid_this_month = decimal_to_float(db.query(func.coalesce(func.sum(func.coalesce(Billing.paid_amount, Billing.amount)), 0)).filter(Billing.is_deleted == False, Billing.status == BillingStatus.PAID, func.extract('month', Billing.payment_date) == now.month, func.extract('year', Billing.payment_date) == now.year).scalar())
    return FinancialSummary(
        active_plans=active_plans,
        active_contracts=active_contracts,
        pending_billings=pending_billings,
        overdue_billings=overdue_billings,
        pending_amount=round(pending_amount, 2),
        overdue_amount=round(overdue_amount, 2),
        paid_this_month=round(paid_this_month, 2),
    )


@router.get('/reports/revenue', response_model=list[RevenueReportItem])
def revenue_report(period: str = Query(default='monthly', pattern='^(monthly|quarterly|annual)$'), db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    rows = db.query(Billing).filter(Billing.is_deleted == False).all()
    buckets: dict[str, dict[str, float]] = defaultdict(lambda: {'total_received': 0.0, 'total_billed': 0.0, 'total_outstanding': 0.0})
    for row in rows:
        reference = row.payment_date or row.due_date
        label = period_bucket(reference, period)
        buckets[label]['total_billed'] += decimal_to_float(row.amount)
        if row.status == BillingStatus.PAID:
            buckets[label]['total_received'] += decimal_to_float(row.paid_amount or row.amount)
        elif row.status in (BillingStatus.PENDING, BillingStatus.OVERDUE):
            buckets[label]['total_outstanding'] += decimal_to_float(row.amount)
    return [RevenueReportItem(label=label, **{k: round(v, 2) for k, v in totals.items()}) for label, totals in sorted(buckets.items())]


@router.get('/reports/delinquent', response_model=list[DelinquentClientItem])
def delinquent_report(db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    rows = (
        db.query(Client.id, Client.name, func.coalesce(func.sum(Billing.amount), 0), func.count(Billing.id))
        .join(
            Billing,
            func.coalesce(Billing.payer_client_id, Billing.client_id) == Client.id,
        )
        .filter(Client.is_deleted == False, Billing.is_deleted == False, Billing.status == BillingStatus.OVERDUE)
        .group_by(Client.id, Client.name)
        .order_by(func.sum(Billing.amount).desc())
        .all()
    )
    return [DelinquentClientItem(client_id=row[0], client_name=row[1], total_open=round(decimal_to_float(row[2]), 2), overdue_count=row[3]) for row in rows]


@router.get('/{item_id}/changes', response_model=list[BillingChangeLogOut])
def billing_changes(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    return db.query(BillingChangeLog).filter(BillingChangeLog.billing_id == item_id).order_by(BillingChangeLog.created_at.desc()).all()


@router.get('/exports/csv')
def export_csv(search: str | None = None, status: str | None = None, client_id: int | None = None, contract_id: int | None = None, due_from: date | None = None, due_to: date | None = None, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    rows = apply_filters(base_query(db), search, status, client_id, contract_id, due_from, due_to).order_by(Billing.due_date.desc()).all()
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['ID', 'Cliente atendido', 'Responsável financeiro', 'Veículo', 'Rastreador', 'Título', 'Tipo', 'Valor', 'Vencimento', 'Status', 'Recebido em', 'Recibo'])
    for row in rows:
        item = serialize_billing(row)
        writer.writerow([item.id, item.client_name, item.payer_name or item.client_name, item.vehicle_plate or '', item.tracker_identifier or '', item.title or item.plan_name or '', item.billing_type, item.amount, item.due_date, item.status, item.payment_date or '', item.receipt_number or ''])
    buffer.seek(0)
    return StreamingResponse(iter([buffer.getvalue()]), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename=financeiro.csv'})


@router.get('/exports/xlsx')
def export_xlsx(search: str | None = None, status: str | None = None, client_id: int | None = None, contract_id: int | None = None, due_from: date | None = None, due_to: date | None = None, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    rows = apply_filters(base_query(db), search, status, client_id, contract_id, due_from, due_to).order_by(Billing.due_date.desc()).all()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Financeiro'
    sheet.append(['ID', 'Cliente atendido', 'Responsável financeiro', 'Veículo', 'Rastreador', 'Título', 'Tipo', 'Valor', 'Vencimento', 'Status', 'Recebido em', 'Recibo'])
    for row in rows:
        item = serialize_billing(row)
        sheet.append([item.id, item.client_name, item.payer_name or item.client_name, item.vehicle_plate or '', item.tracker_identifier or '', item.title or item.plan_name or '', item.billing_type, item.amount, str(item.due_date), item.status, str(item.payment_date or ''), item.receipt_number or ''])
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename=financeiro.xlsx'})


def _receipt_pdf(billing: Billing, client: Client | None) -> BytesIO:
    """
    Recibo no layout aprovado pelo cliente (logo, CNPJ, dados da empresa, box
    do pagador, tabela de itens e assinatura) — o mesmo que sai no topo do
    boleto, reaproveitado de boleto_pdf.
    """
    from decimal import Decimal

    from app.services.boleto_ailos import DadosBoleto
    from app.services.boleto_pdf import gerar_recibo_pdf

    valor = Decimal(str(decimal_to_float(billing.paid_amount or billing.amount)))
    endereco = ' '.join(filter(None, [
        (client.address_line if client else None),
        (client.address_number if client else None),
        (client.neighborhood if client else None),
    ])) if client else ''

    dados = DadosBoleto(
        billing_id=billing.id,
        # Campos da ficha de compensação não são usados no recibo, mas a
        # dataclass os exige — o recibo avulso não desenha código de barras.
        nosso_numero='', nosso_numero_dv='', nosso_numero_display='',
        codigo_barras='', linha_digitavel='',
        data_emissao=billing.payment_date or date.today(),
        data_vencimento=billing.due_date,
        valor=valor,
        sacado_nome=(client.name if client else '') or '',
        sacado_cpf_cnpj=(client.cpf_cnpj if client else '') or '',
        sacado_endereco=endereco,
        sacado_cidade=(client.city if client else '') or '',
        sacado_cep=(client.zip_code if client else '') or '',
        sacado_uf=(client.state if client else '') or '',
        sacado_ie=(getattr(client, 'rg_ie', '') if client else '') or '',
        itens=[(billing.title or billing.notes or 'Cobrança', float(valor))],
    )
    return BytesIO(gerar_recibo_pdf(dados))


@router.get('/{item_id}/receipt')
def download_receipt(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    row = base_query(db).filter(Billing.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail='Cobrança não encontrada')
    # base_query retorna 6 colunas; só a primeira interessa aqui
    billing, *_ = row
    if billing.status != BillingStatus.PAID:
        raise HTTPException(status_code=400, detail='Recibo disponível apenas para cobranças pagas')
    # Recibo em nome de quem pagou = interveniente do contrato, quando houver.
    client = resolver_pagador(db, billing, db.get(Client, billing.client_id))
    buffer = _receipt_pdf(billing, client)
    filename = f'recibo-{billing.receipt_number or item_id}.pdf'
    return StreamingResponse(buffer, media_type='application/pdf', headers={'Content-Disposition': f'inline; filename={filename}'})


@router.get('/', response_model=list[BillingOut])
def list_items(search: str | None = None, status: str | None = None, client_id: int | None = None, contract_id: int | None = None, vehicle_id: int | None = None, due_from: date | None = None, due_to: date | None = None, limit: int = Query(default=200, ge=1, le=1000), db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    query = apply_filters(base_query(db), search, status, client_id, contract_id, due_from, due_to, vehicle_id).order_by(Billing.due_date.desc(), Billing.id.desc())
    return [serialize_billing(row) for row in query.limit(limit).all()]


@router.post('/', response_model=BillingOut)
def create_item(payload: BillingCreate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    if payload.status == BillingStatus.PAID and not payload.payment_date:
        raise HTTPException(
            status_code=400,
            detail='Data de pagamento é obrigatória para criar uma cobrança paga.',
        )
    client = db.get(Client, payload.client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail='Cliente não encontrado')
    if payload.contract_id:
        contract = db.get(Contract, payload.contract_id)
        if not contract or contract.is_deleted:
            raise HTTPException(status_code=404, detail='Contrato não encontrado')
        if contract.client_id != payload.client_id:
            raise HTTPException(status_code=400, detail='O contrato selecionado não pertence ao cliente informado.')
        try:
            data_payer_id = contract_payer_client_id(db, contract)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    else:
        data_payer_id = payload.payer_client_id or payload.client_id
        payer = db.get(Client, data_payer_id)
        if not payer or payer.is_deleted:
            raise HTTPException(status_code=404, detail='Responsável financeiro não encontrado')
    if payload.item_id:
        charge_item = db.get(ClientChargeItem, payload.item_id)
        if not charge_item or charge_item.is_deleted:
            raise HTTPException(status_code=404, detail='Item de cobrança não encontrado')
        if charge_item.client_id != payload.client_id:
            raise HTTPException(status_code=400, detail='O item selecionado não pertence ao cliente informado.')
        if payload.contract_id and charge_item.contract_id and charge_item.contract_id != payload.contract_id:
            raise HTTPException(status_code=400, detail='O item selecionado pertence a outro contrato.')
        if charge_item.contract_id:
            try:
                data_payer_id = charge_item_payer_client_id(db, charge_item)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
    data = payload.model_dump()
    data['payer_client_id'] = data_payer_id
    if not data.get('period_label'):
        data['period_label'] = payload.due_date.strftime('%m/%Y')
    obj = Billing(**data)
    db.add(obj)
    db.flush()
    if obj.status == BillingStatus.PAID:
        obj.receipt_number = obj.receipt_number or generate_receipt_number(obj.id)
        obj.paid_amount = obj.paid_amount or obj.amount
        refresh_charge_items_for_billing(
            db, obj, completion_date=obj.payment_date, commit=False,
        )
    db.commit()
    db.refresh(obj)
    row = base_query(db).filter(Billing.id == obj.id).first()
    return serialize_billing(row)


class ParcelarContratoIn(BaseModel):
    contract_id: int
    num_parcelas: int = Field(ge=2, le=60)
    valor_parcela: float | None = None       # padrão: valor do plano do contrato
    primeiro_vencimento: date | None = None  # padrão: próximo dia de vencimento do contrato


@router.post('/parcelar', response_model=list[BillingOut])
def parcelar_contrato(payload: ParcelarContratoIn, db: Session = Depends(get_db),
                      _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    """Cria N parcelas (boletos) de um contrato — vincula ao plano do veículo e à
    quantidade de parcelas — para depois virarem carnê. Cada parcela vale o valor
    do plano (ou o valor informado), com vencimentos mensais."""
    contract = db.get(Contract, payload.contract_id)
    if not contract or contract.is_deleted:
        raise HTTPException(status_code=404, detail='Contrato não encontrado')
    plan = db.get(Plan, contract.plan_id)
    if not plan or plan.is_deleted:
        raise HTTPException(status_code=404, detail='Plano do contrato não encontrado')

    valor = Decimal(str(payload.valor_parcela)) if payload.valor_parcela else Decimal(str(plan.price))
    if valor <= 0:
        raise HTTPException(status_code=422, detail='Valor da parcela deve ser maior que zero.')

    billing_day = contract.billing_day or (payload.primeiro_vencimento.day if payload.primeiro_vencimento else 10)
    if payload.primeiro_vencimento:
        primeiro = payload.primeiro_vencimento
    else:
        hoje = date.today()
        primeiro = normalize_due_date(hoje.replace(day=1), 0 if hoje.day <= billing_day else 1, billing_day, 1)

    try:
        payer_client_id = contract_payer_client_id(db, contract)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    total = payload.num_parcelas
    criados: list[Billing] = []
    for i in range(total):
        venc = add_months(primeiro, i)
        b = Billing(
            contract_id=contract.id,
            client_id=contract.client_id,
            payer_client_id=payer_client_id,
            vehicle_id=getattr(contract, 'vehicle_id', None),
            tracker_id=getattr(contract, 'tracker_id', None),
            title=f'{plan_title(plan)} • parcela {i + 1}/{total}',
            billing_type='carne',
            installment_number=i + 1,
            installment_total=total,
            amount=valor,
            due_date=venc,
            status=BillingStatus.PENDING if venc >= date.today() else BillingStatus.OVERDUE,
            period_label=venc.strftime('%m/%Y'),
            payment_method=getattr(contract, 'payment_method', None) or 'boleto',
        )
        db.add(b)
        criados.append(b)
    db.commit()

    ids = [b.id for b in criados]
    rows = base_query(db).filter(Billing.id.in_(ids)).order_by(Billing.installment_number.asc()).all()
    return [serialize_billing(r) for r in rows]


@router.post('/lote/situacao')
def batch_status(payload: BillingBatchStatusIn, db: Session = Depends(get_db), current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    """Alterar situação de boletos EM LOTE: 'receber' (marca como pagas) ou
    'cancelar'. Cobranças que não estão em aberto são ignoradas e reportadas."""
    if payload.action not in ('receber', 'cancelar'):
        raise HTTPException(status_code=400, detail="action deve ser 'receber' ou 'cancelar'")
    if payload.action == 'receber' and (not payload.payment_date or not payload.payment_method):
        raise HTTPException(status_code=400, detail='payment_date e payment_method são obrigatórios para receber')
    if payload.action == 'cancelar' and not (payload.reason or '').strip():
        raise HTTPException(status_code=400, detail='reason é obrigatório para cancelar')

    abertas = (BillingStatus.PENDING, BillingStatus.OVERDUE)
    processados: list[int] = []
    ignorados: list[int] = []
    # Cobranças canceladas cujo boleto segue registrado na Ailos — o convênio não
    # tem baixa automática; o frontend avisa e o operador dá baixa manual.
    boletos_ativos: list[dict] = []
    ids = list(dict.fromkeys(payload.billing_ids))
    locked_by_id = {billing.id: billing for billing in lock_billings_for_update(db, ids)}
    processable = [
        locked_by_id[bid]
        for bid in ids
        if bid in locked_by_id
        and not locked_by_id[bid].is_deleted
        and locked_by_id[bid].status in abertas
    ]
    _reject_inflight_ailos_billings(db, [billing.id for billing in processable])
    lock_charge_items_for_billings(db, processable)
    for bid in ids:
        b = locked_by_id.get(bid)
        if not b or b.is_deleted or b.status not in abertas:
            ignorados.append(bid)
            continue
        if payload.action == 'receber':
            b.status = BillingStatus.PAID
            b.paid_amount = b.paid_amount or b.amount
            b.payment_date = payload.payment_date
            b.payment_method = payload.payment_method
            b.receipt_number = b.receipt_number or generate_receipt_number(b.id)
            refresh_charge_items_for_billing(
                db, b, completion_date=payload.payment_date, commit=False,
            )
        else:
            b.status = BillingStatus.CANCELED
            marker = f'Cancelada em lote: {payload.reason}'
            ab = db.query(AilosBoleto).filter_by(billing_id=bid).first()
            if ab and ab.linha_digitavel and ab.codigo_barras:
                boletos_ativos.append({'billing_id': bid, 'nosso_numero': ab.nosso_numero})
                marker += (
                    f' | [ATENÇÃO] Boleto Ailos (nosso número {ab.nosso_numero or "—"}) '
                    'segue ativo no banco — baixa manual pendente.'
                )
            b.notes = f'{b.notes} | {marker}' if b.notes else marker
            refresh_charge_items_for_billing(db, b, commit=False)
        processados.append(bid)
    db.commit()
    return {'processados': processados, 'ignorados': ignorados, 'boletos_ativos': boletos_ativos}


@router.post('/lote/manutencao')
def batch_maintenance(payload: BillingBatchMaintIn, db: Session = Depends(get_db), current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    """Manutenção de título EM LOTE: aplica novo vencimento e/ou valor às
    cobranças em aberto, com justificativa gravada no histórico de cada uma."""
    if payload.due_date is None and payload.amount is None:
        raise HTTPException(status_code=400, detail='Informe due_date e/ou amount')
    if not payload.justification.strip():
        raise HTTPException(status_code=400, detail='Justificativa é obrigatória')

    abertas = (BillingStatus.PENDING, BillingStatus.OVERDUE)
    processados: list[int] = []
    ignorados: list[int] = []
    ids = list(dict.fromkeys(payload.billing_ids))
    locked_by_id = {billing.id: billing for billing in lock_billings_for_update(db, ids)}
    processable_ids = [
        bid
        for bid in ids
        if bid in locked_by_id
        and not locked_by_id[bid].is_deleted
        and locked_by_id[bid].status in abertas
    ]
    _reject_registered_ailos_billings(db, processable_ids)
    new_amount = Decimal(str(payload.amount)) if payload.amount is not None else None
    for bid in ids:
        b = locked_by_id.get(bid)
        if not b or b.is_deleted or b.status not in abertas:
            ignorados.append(bid)
            continue
        for field_name, new_value in (('due_date', payload.due_date), ('amount', new_amount)):
            if new_value is None:
                continue
            previous = getattr(b, field_name)
            if previous != new_value:
                db.add(BillingChangeLog(
                    billing_id=b.id,
                    changed_by_user_id=current_user.id,
                    field_name=field_name,
                    previous_value=str(previous),
                    new_value=str(new_value),
                    justification=f'[lote] {payload.justification}',
                ))
                setattr(b, field_name, new_value)
        b.status = (
            BillingStatus.PENDING
            if b.due_date >= hoje()
            else BillingStatus.OVERDUE
        )
        processados.append(bid)
    db.commit()
    return {'processados': processados, 'ignorados': ignorados}


@router.post('/unificar', response_model=BillingOut)
def unify_billings(payload: BillingUnify, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    """Unifica cobranças em aberto do MESMO cliente em um único boleto avulso
    (negociação). As originais são canceladas com referência à nova cobrança."""
    ids = list(dict.fromkeys(payload.billing_ids))
    if len(ids) < 2:
        raise HTTPException(status_code=400, detail='Informe pelo menos duas cobranças diferentes.')
    locked_by_id = {billing.id: billing for billing in lock_billings_for_update(db, ids)}
    billings = [locked_by_id.get(bid) for bid in ids]
    faltando = [bid for bid, b in zip(ids, billings) if not b or b.is_deleted]
    if faltando:
        raise HTTPException(status_code=404, detail=f'Cobranças não encontradas: {faltando}')

    abertas = (BillingStatus.PENDING, BillingStatus.OVERDUE)
    invalidas = [b.id for b in billings if b.status not in abertas]
    if invalidas:
        raise HTTPException(status_code=400, detail=f'Apenas cobranças pendentes/vencidas podem ser unificadas: {invalidas}')
    _reject_registered_ailos_billings(db, ids)

    payer_ids = {
        resolver_pagador(db, billing, db.get(Client, billing.client_id)).id
        for billing in billings
    }
    if len(payer_ids) > 1:
        raise HTTPException(
            status_code=400,
            detail='Todas as cobranças precisam ter o mesmo responsável financeiro.',
        )
    if len({b.client_id for b in billings}) > 1:
        raise HTTPException(status_code=400, detail='Todas as cobranças precisam ser do mesmo cliente atendido.')

    lock_charge_items_for_billings(db, billings)
    total = sum((Decimal(str(b.amount)) for b in billings), Decimal('0.00'))
    refs = ', '.join(f'#{b.id}' for b in billings)
    # Título vai pro boleto/recibo — o cliente vê isso, não os IDs internos.
    # "#12, #13" não diz nada pra quem recebe; quantidade + período de
    # referência é o que de fato identifica a negociação.
    periodos = sorted({b.period_label for b in billings if b.period_label}, key=_period_label_sort_key)
    faixa = f'{periodos[0]} A {periodos[-1]}' if len(periodos) >= 2 else (periodos[0] if periodos else '')
    titulo = f'NEGOCIAÇÃO — {len(billings)} PARCELA(S) EM ABERTO' + (f' (REF. {faixa})' if faixa else '')
    nova = Billing(
        client_id=billings[0].client_id,
        payer_client_id=next(iter(payer_ids)),
        billing_type='avulsa',
        title=titulo,
        amount=Decimal(str(payload.amount)) if payload.amount else total,
        due_date=payload.due_date,
        status=(BillingStatus.PENDING if payload.due_date >= hoje() else BillingStatus.OVERDUE),
        period_label=payload.due_date.strftime('%m/%Y'),
        notes=payload.notes or f'Negociação: unifica {refs}. Soma original: R$ {total:.2f}.',
    )
    db.add(nova)
    db.flush()
    transfer_charge_items_to_billing(db, billings, nova)
    for b in billings:
        b.status = BillingStatus.CANCELED
        marker = f'Unificada na cobrança #{nova.id}.'
        b.notes = f'{b.notes} | {marker}' if b.notes else marker
        refresh_charge_items_for_billing(db, b, commit=False)
    db.commit()
    row = base_query(db, refresh_statuses=False).filter(Billing.id == nova.id).first()
    return serialize_billing(row)


@router.get('/{item_id}', response_model=BillingOut)
def get_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    row = base_query(db).filter(Billing.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail='Cobrança não encontrada')
    return serialize_billing(row)


@router.post('/{item_id}/receive', response_model=BillingOut)
def receive_billing(item_id: int, payload: BillingReceive, db: Session = Depends(get_db), current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    billing = _lock_billing_or_404(db, item_id)
    if billing.status == BillingStatus.CANCELED:
        raise HTTPException(status_code=400, detail='Cobrança cancelada não pode ser recebida.')
    if billing.status == BillingStatus.PAID:
        raise HTTPException(status_code=400, detail='Cobrança já está paga.')
    _reject_inflight_ailos_billings(db, [billing.id])
    lock_charge_items_for_billings(db, [billing])
    marcar_billing_pago(
        db, billing,
        payment_date=payload.payment_date,
        paid_amount=payload.paid_amount,
        payment_method=payload.payment_method,
        notes=payload.notes,
        lock=False,
    )
    row = base_query(db).filter(Billing.id == billing.id).first()
    return serialize_billing(row)


@router.post('/{item_id}/cancel', response_model=BillingOut)
def cancel_billing(item_id: int, payload: BillingCancel, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    billing = _lock_billing_or_404(db, item_id)
    if billing.status == BillingStatus.CANCELED:
        raise HTTPException(status_code=400, detail='Cobrança já está cancelada.')
    if billing.status == BillingStatus.PAID:
        raise HTTPException(status_code=400, detail='Cobrança paga não pode ser cancelada. Use estorno se precisar reverter o pagamento.')
    _reject_inflight_ailos_billings(db, [billing.id])

    # Boleto já registrado na Ailos continua pagável no banco após o
    # cancelamento — o convênio não expõe baixa automática. Avisa e exige
    # confirmação explícita para o operador não esquecer a baixa manual.
    ab = db.query(AilosBoleto).filter_by(billing_id=item_id).first()
    boleto_no_banco = bool(ab and ab.linha_digitavel and ab.codigo_barras)
    if boleto_no_banco and not payload.confirmar_boleto_ailos:
        raise HTTPException(
            status_code=409,
            detail={
                'code': 'boleto_ailos_registrado',
                'nosso_numero': ab.nosso_numero,
                'message': (
                    f'Há um boleto registrado na Ailos (nosso número {ab.nosso_numero or "—"}) '
                    'para esta cobrança. O cancelamento interrompe a cobrança no sistema e '
                    'desativa o link público, mas o título continua ativo no banco — o convênio '
                    'não oferece baixa automática. Dê baixa manualmente na Ailos para o cliente '
                    'não conseguir pagar. Cancelar mesmo assim?'
                ),
            },
        )

    lock_charge_items_for_billings(db, [billing])
    billing.status = BillingStatus.CANCELED
    nota_extra = ''
    if boleto_no_banco:
        nota_extra = (
            f'\n[ATENÇÃO] Boleto Ailos (nosso número {ab.nosso_numero or "—"}) '
            'segue ativo no banco — baixa manual pendente.'
        )
    billing.notes = f'{billing.notes or ""}\nCancelada: {payload.reason}{nota_extra}'.strip()
    refresh_charge_items_for_billing(db, billing, commit=False)
    db.commit()
    db.refresh(billing)
    row = base_query(db).filter(Billing.id == billing.id).first()
    return serialize_billing(row)


@router.put('/{item_id}', response_model=BillingOut)
def update_item(item_id: int, payload: BillingUpdate, db: Session = Depends(get_db), current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    billing = _lock_billing_or_404(db, item_id)
    # Estados terminais são imutáveis pelo PUT genérico: alterar valor/vencimento/
    # status de cobrança paga ou cancelada burlaria a máquina de estados (receber →
    # estornar; cancelar tem fluxo próprio).
    if billing.status in (BillingStatus.PAID, BillingStatus.CANCELED):
        raise HTTPException(
            status_code=400,
            detail='Cobrança paga ou cancelada não pode ser alterada.',
        )
    data = payload.model_dump(exclude_unset=True)
    justification = data.pop('justification', None)

    immutable_links = {
        'client_id', 'payer_client_id', 'contract_id', 'item_id',
        'vehicle_id', 'tracker_id',
    }
    if immutable_links.intersection(data):
        raise HTTPException(
            status_code=400,
            detail='Cliente, responsável financeiro, contrato, item, veículo e rastreador da cobrança não podem ser alterados após a emissão.',
        )

    # Transição de status tem fluxo próprio (Receber/Cancelar), com as travas da
    # máquina de estados e o aviso de boleto Ailos. O PUT genérico não muda status.
    if 'status' in data:
        raise HTTPException(
            status_code=400,
            detail='Mudança de situação deve usar Receber ou Cancelar, não a edição da cobrança.',
        )

    if ('amount' in data or 'due_date' in data) and not justification:
        raise HTTPException(status_code=400, detail='Justificativa é obrigatória para alterar valor ou vencimento.')
    if 'amount' in data or 'due_date' in data:
        _reject_registered_ailos_billings(db, [billing.id])

    for field_name in ['amount', 'due_date']:
        if field_name in data:
            previous_value = getattr(billing, field_name)
            new_value = data[field_name]
            if previous_value != new_value:
                db.add(BillingChangeLog(
                    billing_id=billing.id,
                    changed_by_user_id=current_user.id,
                    field_name=field_name,
                    previous_value=str(previous_value),
                    new_value=str(new_value),
                    justification=justification or 'Atualização administrativa',
                ))

    for key, value in data.items():
        setattr(billing, key, value)

    if billing.status == BillingStatus.PAID and not billing.receipt_number:
        billing.receipt_number = generate_receipt_number(billing.id)
    db.commit()
    db.refresh(billing)
    row = base_query(db).filter(Billing.id == billing.id).first()
    return serialize_billing(row)


@router.delete('/{item_id}')
def delete_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    obj = _lock_billing_or_404(db, item_id)
    if obj.status == BillingStatus.PAID:
        raise HTTPException(
            status_code=400,
            detail='Cobrança paga não pode ser removida; preserve o histórico financeiro.',
        )
    _reject_inflight_ailos_billings(db, [obj.id])
    lock_charge_items_for_billings(db, [obj])
    obj.is_deleted = True
    refresh_charge_items_for_billing(db, obj, commit=False)
    db.commit()
    return {'message': 'Cobrança removida com sucesso'}
