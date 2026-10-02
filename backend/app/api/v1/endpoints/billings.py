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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.api.deps import require_roles
from app.core.integrity import raise_integrity_conflict
from app.core.timezone import hoje
from app.db.session import get_db
from app.models.ailos_boleto import AilosBoleto
from app.models.billing import RECURRING_BILLING_TYPES, Billing
from app.models.billing_change_log import BillingChangeLog
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.cnab_remessa import CnabRemessaItem
from app.models.contract import Contract
from app.models.enums import BillingStatus, UserRole
from app.models.plan import Plan
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.models.user import User
from app.services import recebimento, titulo_bancario
from app.services.ailos_boletos import resolver_pagador
from app.schemas.billing import (
    BillingAdjustmentOut,
    BillingBatchMaintIn,
    BillingBatchStatusIn,
    BillingCancel,
    BillingChangeLogOut,
    BillingCreate,
    BillingOut,
    BillingReceive,
    BillingRefund,
    BillingReleasePeriod,
    BillingUnify,
    BillingUpdate,
    DelinquentClientItem,
    FinancialSummary,
    RevenueReportItem,
)
from app.services.financial import (
    BillingSubstitutionError,
    add_months,
    charge_item_payer_client_id,
    decimal_to_float,
    ensure_substitution_allows_removal,
    existing_recurring_periods,
    generate_receipt_number,
    lock_charge_items_for_billings,
    lock_billings_for_update,
    marcar_billing_pago,
    mark_billings_substituted,
    normalize_due_date,
    period_bucket,
    plan_title,
    refresh_overdue_statuses,
    refresh_charge_items_for_billing,
    release_billing_competencia,
    restore_substituted_originals,
    substituted_originals,
    transfer_charge_items_to_billing,
    valor_com_juros,
    contract_payer_client_id,
)

router = APIRouter()

def _exigir(db: Session, operacao: str, billing_ids: list[int], **kwargs) -> dict:
    """Política bancária única (app/services/titulo_bancario.py) → 409."""
    try:
        return titulo_bancario.exigir(db, operacao, billing_ids, **kwargs)
    except titulo_bancario.PoliticaBancariaError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.detail()) from exc


# Barreiras do banco que uma corrida pode acionar mesmo depois da checagem da
# aplicação (duas requisições checam ao mesmo tempo, uma confirma primeiro).
# Viram 409 com mensagem de domínio em vez de 500 genérico.
_BILLING_CONFLICTS = {
    'uq_billings_contract_competencia_recorrente': (
        'Este contrato já tem mensalidade lançada para esta competência '
        '(outra operação acabou de gravá-la). Atualize a tela e confira antes de repetir.'
    ),
    'uq_billings_item_parcela_efetiva': (
        'Esta parcela do serviço já foi gerada por outra operação. Atualize a tela.'
    ),
}
_BILLING_CONFLICTS_SQLITE = {
    'billings.contract_id, billings.competencia': 'uq_billings_contract_competencia_recorrente',
    'billings.item_id, billings.installment_number': 'uq_billings_item_parcela_efetiva',
}


def _commit_billing_write(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        raise_integrity_conflict(
            db, exc, _BILLING_CONFLICTS, sqlite_columns=_BILLING_CONFLICTS_SQLITE,
        )


def _substitution_conflict(
    exc: BillingSubstitutionError | titulo_bancario.PoliticaBancariaError,
) -> HTTPException:
    return HTTPException(status_code=409, detail=exc.detail())


def _encerrar_pendencia_resolvida_no_cancelamento(db: Session, billing_id: int, user_id: int | None) -> None:
    """Título baixado no banco com a cobrança aberta aqui: cancelar a
    cobrança é justamente o tratamento — a divergência deixa de existir."""
    boleto = db.query(AilosBoleto).filter_by(billing_id=billing_id).first()
    if boleto is None or boleto.pendencia != 'baixado_com_cobranca_aberta':
        return
    db.add(BillingChangeLog(
        billing_id=billing_id, changed_by_user_id=user_id, field_name='pendencia_conciliacao',
        previous_value=boleto.pendencia, new_value=None,
        justification='Resolvida pelo cancelamento da cobrança (título já baixado no banco).',
    ))
    boleto.pendencia = None
    boleto.pendencia_detalhe = None
    boleto.pendencia_desde = None


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


def base_query(db: Session, *, refresh_statuses: bool = False):
    # Reclassificação pendente<->vencida roda como worker de hora em hora (BE-05,
    # ver main.py); due_date é granularidade de dia, então até 1h de atraso na
    # reclassificação não afeta regra de negócio nenhuma. refresh_statuses=True
    # fica disponível para quem realmente precisa de leitura imediatamente
    # consistente logo após alterar o próprio status de uma cobrança.
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
    # Cadastros removidos NÃO escondem a cobrança (FIN-10): excluir contrato,
    # veículo, plano, rastreador ou cliente é baixa operacional, e o
    # histórico financeiro — inclusive recibo de cobrança paga — continua
    # consultável por quem já tem acesso ao financeiro. A cobrança sai só
    # quando ela própria é removida. Os flags viram ``relacoes_removidas``.
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
            AilosBoleto,
            CnabRemessaItem.nosso_numero.label('cnab_nosso_numero'),
            Client.is_deleted.label('client_removed'),
            Payer.is_deleted.label('payer_removed'),
            Contract.is_deleted.label('contract_removed'),
            Plan.is_deleted.label('plan_removed'),
            Vehicle.is_deleted.label('vehicle_removed'),
            Tracker.is_deleted.label('tracker_removed'),
        )
        .join(Client, Client.id == Billing.client_id)
        .join(Payer, Payer.id == func.coalesce(Billing.payer_client_id, Billing.client_id))
        .outerjoin(Contract, Contract.id == Billing.contract_id)
        .outerjoin(Plan, Plan.id == Contract.plan_id)
        .outerjoin(Vehicle, Vehicle.id == Billing.vehicle_id)
        .outerjoin(Tracker, Tracker.id == Billing.tracker_id)
        .outerjoin(AilosBoleto, AilosBoleto.billing_id == Billing.id)
        .outerjoin(
            CnabRemessaItem,
            and_(CnabRemessaItem.billing_id == Billing.id, CnabRemessaItem.status == 'reservado'),
        )
        .filter(Billing.is_deleted.is_(False))
    )


_RELACOES = ('cliente', 'responsavel_financeiro', 'contrato', 'plano', 'veiculo', 'rastreador')


def _titulo_bancario_out(billing_id: int, boleto: AilosBoleto | None, cnab_nosso_numero: str | None) -> dict | None:
    estado = titulo_bancario.estado_do_boleto(boleto)
    if estado == titulo_bancario.SEM_TITULO:
        if cnab_nosso_numero is None:
            return None
        return titulo_bancario.TituloBancario(
            billing_id=billing_id, estado=titulo_bancario.REMESSA_CNAB, canal='cnab',
            nosso_numero=cnab_nosso_numero,
        ).as_dict()
    return titulo_bancario.TituloBancario(
        billing_id=billing_id, estado=estado, canal='ailos_api',
        nosso_numero=boleto.nosso_numero, baixa_status=boleto.baixa_status, pendencia=boleto.pendencia,
    ).as_dict()


def serialize_billing(row) -> BillingOut:
    (
        billing, client_name, payer_name, plan_name, contract_status,
        vehicle_plate, tracker_identifier, boleto_ailos, ailos_boleto, cnab_nosso_numero,
        *removidas,
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
        sgr_payload=billing.sgr_payload,
        period_label=billing.period_label,
        competencia=billing.competencia,
        competencia_liberada=bool(billing.competencia_liberada),
        substituted_by_id=billing.substituted_by_id,
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
        titulo_bancario=_titulo_bancario_out(billing.id, ailos_boleto, cnab_nosso_numero),
        relacoes_removidas=[nome for nome, removida in zip(_RELACOES, removidas) if removida],
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
    # Reclassificação pendente<->vencida roda no worker horário (BE-05) — não
    # precisa ser refeita a cada leitura do resumo (ver base_query em cima).
    active_plans = db.query(func.count(Plan.id)).filter(Plan.is_deleted == False, Plan.active == True).scalar() or 0
    active_contracts = db.query(func.count(Contract.id)).filter(Contract.is_deleted == False, Contract.status == 'ativo').scalar() or 0
    now = date.today()
    month_start = now.replace(day=1)
    next_month = date(now.year + 1, 1, 1) if now.month == 12 else date(now.year, now.month + 1, 1)
    pending = Billing.status == BillingStatus.PENDING
    overdue = Billing.status == BillingStatus.OVERDUE
    paid_this_month_filter = and_(
        Billing.status == BillingStatus.PAID,
        Billing.payment_date >= month_start,
        Billing.payment_date < next_month,
    )
    pending_billings, overdue_billings, pending_amount, overdue_amount, paid_this_month = (
        db.query(
            func.count(case((pending, Billing.id))),
            func.count(case((overdue, Billing.id))),
            func.coalesce(func.sum(case((pending, Billing.amount), else_=0)), 0),
            func.coalesce(func.sum(case((overdue, Billing.amount), else_=0)), 0),
            func.coalesce(func.sum(case((paid_this_month_filter, func.coalesce(Billing.paid_amount, Billing.amount)), else_=0)), 0),
        )
        .filter(Billing.is_deleted.is_(False))
        .one()
    )
    return FinancialSummary(
        active_plans=active_plans,
        active_contracts=active_contracts,
        pending_billings=pending_billings,
        overdue_billings=overdue_billings,
        pending_amount=round(decimal_to_float(pending_amount), 2),
        overdue_amount=round(decimal_to_float(overdue_amount), 2),
        paid_this_month=round(decimal_to_float(paid_this_month), 2),
    )


@router.get('/reports/revenue', response_model=list[RevenueReportItem])
def revenue_report(period: str = Query(default='monthly', pattern='^(monthly|quarterly|annual)$'), db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    """Série do gráfico "Faturamento mensal" do Financeiro (PROD-01).

    Cada campo tem UMA base temporal (docs/financeiro/dicionario-metricas.md):

    * ``total_billed`` — emitido: valor das cobranças NÃO canceladas, pelo
      mês de VENCIMENTO;
    * ``total_outstanding`` — em aberto (pendente/vencida), pelo VENCIMENTO;
    * ``total_received_by_due`` — quanto do emitido naquele vencimento já foi
      pago (mesma base do emitido: dá a taxa de recebimento);
    * ``total_received`` — CAIXA: valor pago, pelo mês do PAGAMENTO.

    Antes, cobrança cancelada somava no emitido (consolidação e negociação
    contavam a dívida duas vezes) e o emitido de cobrança paga ia para o mês
    do pagamento, misturando as bases no mesmo campo.
    """
    # Agrega no banco: carregar cada Billing completo para somar em Python
    # transferia e instanciava toda a carteira a cada abertura do Financeiro.
    due_year = func.extract('year', Billing.due_date)
    due_month = func.extract('month', Billing.due_date)
    received = func.coalesce(Billing.paid_amount, Billing.amount)
    due_rows = (
        db.query(
            due_year, due_month,
            func.sum(Billing.amount),
            func.sum(case((Billing.status == BillingStatus.PAID, received), else_=0)),
            func.sum(case((Billing.status.in_((BillingStatus.PENDING, BillingStatus.OVERDUE)), Billing.amount), else_=0)),
        )
        .filter(Billing.is_deleted.is_(False), Billing.status != BillingStatus.CANCELED)
        .group_by(due_year, due_month)
        .all()
    )
    cash_date = func.coalesce(Billing.payment_date, Billing.due_date)
    cash_year = func.extract('year', cash_date)
    cash_month = func.extract('month', cash_date)
    cash_rows = (
        db.query(cash_year, cash_month, func.sum(received))
        .filter(Billing.is_deleted.is_(False), Billing.status == BillingStatus.PAID)
        .group_by(cash_year, cash_month)
        .all()
    )
    campos = ('total_received', 'total_billed', 'total_outstanding', 'total_received_by_due')
    buckets: dict[str, dict[str, Decimal]] = defaultdict(lambda: {k: Decimal('0.00') for k in campos})
    for year, month, billed, received_by_due, outstanding in due_rows:
        label = period_bucket(date(int(year), int(month), 1), period)
        buckets[label]['total_billed'] += Decimal(str(billed))
        buckets[label]['total_received_by_due'] += Decimal(str(received_by_due))
        buckets[label]['total_outstanding'] += Decimal(str(outstanding))
    for year, month, received_cash in cash_rows:
        label = period_bucket(date(int(year), int(month), 1), period)
        buckets[label]['total_received'] += Decimal(str(received_cash))
    return [
        RevenueReportItem(label=label, **{k: decimal_to_float(v) for k, v in totals.items()})
        for label, totals in sorted(buckets.items(), key=lambda kv: _bucket_sort_key(kv[0]))
    ]


def _bucket_sort_key(label: str) -> tuple:
    """'MM/AAAA' ordena por ano e mês (texto invertia a virada do ano)."""
    if '/' in label:
        mes, ano = label.split('/', 1)
        return (ano, mes)
    return (label[:4], label)


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


def _billings_do_mesmo_recibo(db: Session, billing: Billing) -> list[Billing]:
    """Todas as parcelas pagas na mesma operação de recebimento (mesmo
    receipt_number) — para o recibo discriminar cada parcela da operação em
    vez de mostrar só a que foi usada para abrir o download."""
    if not billing.receipt_number:
        return [billing]
    rows = (
        db.query(Billing)
        .filter(
            Billing.receipt_number == billing.receipt_number,
            Billing.is_deleted.is_(False),
            Billing.status == BillingStatus.PAID,
        )
        .order_by(Billing.id.asc())
        .all()
    )
    return rows or [billing]


def _receipt_pdf(billings: list[Billing], client: Client | None) -> BytesIO:
    """
    Recibo no layout aprovado pelo cliente (logo, CNPJ, dados da empresa, box
    do pagador, tabela de itens e assinatura) — o mesmo que sai no topo do
    boleto, reaproveitado de boleto_pdf.

    Recebe uma ou mais cobranças: um pagamento em lote gera um único recibo
    consolidado, com uma linha por parcela e o total da operação.
    """
    from decimal import Decimal

    from app.services.boleto_ailos import DadosBoleto
    from app.services.boleto_pdf import gerar_recibo_pdf

    primeira = billings[0]
    itens = [
        (b.title or b.notes or 'Cobrança', decimal_to_float(b.paid_amount or b.amount))
        for b in billings
    ]
    valor = Decimal(str(sum(item[1] for item in itens)))
    endereco = ' '.join(filter(None, [
        (client.address_line if client else None),
        (client.address_number if client else None),
        (client.neighborhood if client else None),
    ])) if client else ''

    dados = DadosBoleto(
        billing_id=primeira.id,
        # Campos da ficha de compensação não são usados no recibo, mas a
        # dataclass os exige — o recibo avulso não desenha código de barras.
        nosso_numero='', nosso_numero_dv='', nosso_numero_display='',
        codigo_barras='', linha_digitavel='',
        data_emissao=primeira.payment_date or date.today(),
        data_vencimento=primeira.due_date,
        valor=valor,
        sacado_nome=(client.name if client else '') or '',
        sacado_cpf_cnpj=(client.cpf_cnpj if client else '') or '',
        sacado_endereco=endereco,
        sacado_cidade=(client.city if client else '') or '',
        sacado_cep=(client.zip_code if client else '') or '',
        sacado_uf=(client.state if client else '') or '',
        sacado_ie=(getattr(client, 'rg_ie', '') if client else '') or '',
        itens=itens,
    )
    return BytesIO(gerar_recibo_pdf(dados))


@router.get('/{item_id}/receipt')
def download_receipt(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    row = base_query(db).filter(Billing.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail='Cobrança não encontrada')
    # só a primeira coluna de base_query interessa aqui
    billing, *_ = row
    if billing.status != BillingStatus.PAID:
        raise HTTPException(status_code=400, detail='Recibo disponível apenas para cobranças pagas')
    # Recibo em nome de quem pagou = interveniente do contrato, quando houver.
    # Recibo é documento histórico: sai mesmo se o cadastro foi removido depois.
    client = resolver_pagador(
        db, billing, db.get(Client, billing.client_id), permitir_removido=True,
    )
    grupo = _billings_do_mesmo_recibo(db, billing)
    buffer = _receipt_pdf(grupo, client)
    filename = f'recibo-{billing.receipt_number or item_id}.pdf'
    return StreamingResponse(buffer, media_type='application/pdf', headers={'Content-Disposition': f'inline; filename={filename}'})


@router.get('/', response_model=list[BillingOut])
def list_items(search: str | None = None, status: str | None = None, client_id: int | None = None, contract_id: int | None = None, vehicle_id: int | None = None, due_from: date | None = None, due_to: date | None = None, limit: int = Query(default=200, ge=1, le=1000), db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    query = apply_filters(base_query(db), search, status, client_id, contract_id, due_from, due_to, vehicle_id).order_by(Billing.due_date.desc(), Billing.id.desc())
    return [serialize_billing(row) for row in query.limit(limit).all()]


def _validate_asset_links(
    db: Session, client_id: int, vehicle_id: int | None, tracker_id: int | None,
) -> None:
    """Veículo e rastreador da cobrança precisam ser do cliente atendido
    (DB-02). Mesmas regras de client_charge_items.validate_links."""
    if vehicle_id:
        vehicle = db.get(Vehicle, vehicle_id)
        if not vehicle or vehicle.is_deleted:
            raise HTTPException(status_code=404, detail='Veículo não encontrado')
        if vehicle.client_id != client_id:
            raise HTTPException(status_code=400, detail='O veículo selecionado não pertence ao cliente informado.')
    if tracker_id:
        tracker = db.get(Tracker, tracker_id)
        if not tracker or tracker.is_deleted:
            raise HTTPException(status_code=404, detail='Rastreador não encontrado')
        if tracker.client_id and tracker.client_id != client_id:
            raise HTTPException(status_code=400, detail='O rastreador selecionado não pertence ao cliente informado.')
        if vehicle_id and tracker.vehicle_id and tracker.vehicle_id != vehicle_id:
            raise HTTPException(status_code=400, detail='O rastreador selecionado está vinculado a outro veículo.')


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
        if payload.billing_type in RECURRING_BILLING_TYPES:
            # Mesmo protocolo do carnê e do fechamento: quem cria a mensalidade
            # de um contrato trava o contrato ANTES de conferir o mês. Sem
            # isto, carnê (contrato travado, esperando o índice) e este INSERT
            # (índice ocupado, esperando o contrato na checagem da FK) davam
            # deadlock → 500. Travado, o segundo espera e recebe 409.
            contract = (
                db.query(Contract)
                .filter(Contract.id == payload.contract_id)
                .with_for_update()
                .populate_existing()
                .first()
            )
        else:
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
    _validate_asset_links(db, payload.client_id, payload.vehicle_id, payload.tracker_id)
    data = payload.model_dump()
    data['payer_client_id'] = data_payer_id
    if not data.get('period_label'):
        data['period_label'] = payload.due_date.strftime('%m/%Y')
    if payload.contract_id and payload.billing_type in RECURRING_BILLING_TYPES:
        # Mesma regra do carnê e do fechamento, antes do INSERT: sem isto o
        # índice único devolvia 500 (e '9/2026' passava ao lado de '09/2026').
        if existing_recurring_periods(db, payload.contract_id, [data['period_label']]):
            raise HTTPException(
                status_code=409,
                detail={
                    'code': 'competencia_ocupada',
                    'message': (
                        f'O contrato #{payload.contract_id} já tem mensalidade lançada para '
                        f'{data["period_label"]}. Para cobrar esse mês de novo, libere a '
                        'competência da cobrança cancelada ou lance como avulsa.'
                    ),
                },
            )
    obj = Billing(**data)
    db.add(obj)
    try:
        db.flush()
    except IntegrityError as exc:
        raise_integrity_conflict(db, exc, _BILLING_CONFLICTS, sqlite_columns=_BILLING_CONFLICTS_SQLITE)
    if obj.status == BillingStatus.PAID:
        obj.receipt_number = obj.receipt_number or generate_receipt_number(obj.id)
        obj.paid_amount = obj.paid_amount or obj.amount
        refresh_charge_items_for_billing(
            db, obj, completion_date=obj.payment_date, commit=False,
        )
    _commit_billing_write(db)
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
    # Trava o contrato: fecha a corrida com um fechamento mensal concorrente
    # para o MESMO contrato (mesmo padrão de _locked_contracts em
    # billing_closure/recurring.py) — sem isto, um fechamento em andamento
    # podia comitar a mensalidade de um mês entre a checagem de conflito
    # abaixo e a criação das parcelas do carnê.
    contract = db.query(Contract).filter(Contract.id == payload.contract_id).with_for_update().first()
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
    period_labels = [add_months(primeiro, i).strftime('%m/%Y') for i in range(total)]

    # Sem isto, um contrato que já teve a mensalidade de um mês gerada pelo
    # fechamento (ou por um carnê anterior) ganhava uma SEGUNDA cobrança do
    # mesmo mês ao gerar/regerar o carnê — cobrança duplicada em produção.
    conflitos = existing_recurring_periods(db, contract.id, period_labels)
    if conflitos:
        meses_em_ordem = [p for p in period_labels if p in conflitos]
        raise HTTPException(
            status_code=409,
            detail=(
                'Este contrato já tem cobrança de mensalidade lançada para: '
                + ', '.join(meses_em_ordem)
                + '. Cancelada também ocupa o mês. Para cobrar um desses meses no carnê, '
                'cancele a cobrança liberando a competência (ou use "Liberar competência" '
                'numa cobrança já cancelada); ou ajuste o primeiro vencimento.'
            ),
        )

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
            period_label=period_labels[i],
            payment_method=getattr(contract, 'payment_method', None) or 'boleto',
        )
        db.add(b)
        criados.append(b)
    _commit_billing_write(db)

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
    ids = list(dict.fromkeys(payload.billing_ids))
    locked_by_id = {billing.id: billing for billing in lock_billings_for_update(db, ids)}
    processable = [
        locked_by_id[bid]
        for bid in ids
        if bid in locked_by_id
        and not locked_by_id[bid].is_deleted
        and locked_by_id[bid].status in abertas
    ]
    # Em lote, a tela já avisa sobre título ativo pelo retorno boletos_ativos;
    # registro em andamento ou com desfecho desconhecido bloqueia o lote.
    titulos = _exigir(
        db,
        titulo_bancario.RECEBER if payload.action == 'receber' else titulo_bancario.CANCELAR,
        [billing.id for billing in processable],
        confirmado=True,
    )
    if payload.action == 'cancelar':
        substitutos = [b.id for b in processable if substituted_originals(db, b.id)]
        if substitutos:
            raise HTTPException(
                status_code=409,
                detail={
                    'code': 'titulo_substituto',
                    'billing_ids': substitutos,
                    'message': (
                        'Boleto único/negociação não pode ser cancelado em lote: as cobranças '
                        'que ele substituiu ficariam sem nenhuma cobrança em aberto. Cancele '
                        'cada um pelo detalhe, confirmando a reversão.'
                    ),
                },
            )
    lock_charge_items_for_billings(db, processable)
    # Um pagamento em lote é UMA operação: todas as parcelas recebidas juntas
    # compartilham o mesmo número de recibo, para gerar um recibo consolidado
    # em vez de um por parcela. Ancorado na menor id do lote — determinístico
    # e único, já que uma parcela só entra em processable uma vez na vida
    # (depois de paga, sai de `abertas`).
    lote_receipt_number = (
        generate_receipt_number(min(b.id for b in processable))
        if payload.action == 'receber' and processable else None
    )
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
            b.receipt_number = b.receipt_number or lote_receipt_number
            refresh_charge_items_for_billing(
                db, b, completion_date=payload.payment_date, commit=False,
            )
        else:
            b.status = BillingStatus.CANCELED
            marker = f'Cancelada em lote: {payload.reason}'
            titulo = titulos.get(bid)
            if titulo is not None and titulo.ativo_no_banco:
                marker += (
                    f' | [ATENÇÃO] Boleto Ailos (nosso número {titulo.nosso_numero or "—"}) '
                    'segue ativo no banco — baixa manual pendente.'
                )
            b.notes = f'{b.notes} | {marker}' if b.notes else marker
            refresh_charge_items_for_billing(db, b, commit=False)
        processados.append(bid)
    # Cancelada ou recebida por fora com boleto ativo: baixa pendente
    # estrutural — a conciliação continua acompanhando o título (FIN-02).
    boletos_ativos = titulo_bancario.marcar_baixa_pendente(
        db, processados,
        f'cancelada em lote: {payload.reason}' if payload.action == 'cancelar' else 'recebida em lote fora do boleto',
    )
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
    _exigir(db, titulo_bancario.ALTERAR_VALOR, processable_ids)
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
    _exigir(db, titulo_bancario.ALTERAR_VALOR, ids)

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
    mark_billings_substituted(billings, nova, f'Unificada na cobrança #{nova.id}.')
    for b in billings:
        refresh_charge_items_for_billing(db, b, commit=False)
    _commit_billing_write(db)
    row = base_query(db).filter(Billing.id == nova.id).first()
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
    _exigir(db, titulo_bancario.RECEBER, [billing.id])
    lock_charge_items_for_billings(db, [billing])
    try:
        # Diferença entre título e recebido precisa de classificação explícita
        # (desconto, saldo, encargos, crédito) — FIN-06.
        recebimento.registrar_recebimento(
            db, billing,
            paid_amount=payload.paid_amount,
            payment_date=payload.payment_date,
            payment_method=payload.payment_method,
            notes=payload.notes,
            tratamento=payload.tratamento_diferenca,
            justificativa=payload.justificativa_diferenca,
            saldo_vencimento=payload.saldo_vencimento,
            user_id=current_user.id,
        )
    except recebimento.RecebimentoError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc
    row = base_query(db).filter(Billing.id == billing.id).first()
    return serialize_billing(row)


@router.post('/{item_id}/estornar', response_model=BillingOut)
def refund_billing(
    item_id: int,
    payload: BillingRefund,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL)),
):
    """Desfaz um recebimento manual: a cobrança volta a ficar em aberto e o
    pagamento desfeito fica registrado como ajuste ``estorno``."""
    billing = _lock_billing_or_404(db, item_id)
    lock_charge_items_for_billings(db, [billing])
    try:
        recebimento.estornar_recebimento(
            db, billing, user_id=current_user.id, justificativa=payload.justificativa,
        )
    except recebimento.RecebimentoError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc
    row = base_query(db).filter(Billing.id == billing.id).first()
    return serialize_billing(row)


@router.get('/{item_id}/ajustes', response_model=list[BillingAdjustmentOut])
def billing_adjustments(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    """Descontos, saldos, encargos, créditos e estornos da cobrança."""
    return recebimento.ajustes_da_cobranca(db, item_id)


@router.post('/{item_id}/cancel', response_model=BillingOut)
def cancel_billing(item_id: int, payload: BillingCancel, db: Session = Depends(get_db), current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    billing = _lock_billing_or_404(db, item_id)
    if billing.status == BillingStatus.CANCELED:
        raise HTTPException(status_code=400, detail='Cobrança já está cancelada.')
    if billing.status == BillingStatus.PAID:
        raise HTTPException(status_code=400, detail='Cobrança paga não pode ser cancelada. Estorne o recebimento antes, se precisar reverter o pagamento.')
    # Registro em andamento/desfecho desconhecido bloqueia; título ativo no
    # banco exige confirmação (continua pagável até a baixa na Ailos).
    titulos = _exigir(
        db, titulo_bancario.CANCELAR, [billing.id], confirmado=payload.confirmar_boleto_ailos,
    )
    titulo = titulos[billing.id]
    if payload.liberar_competencia and titulo.ativo_no_banco:
        # Liberar o mês com o boleto antigo ainda pagável deixaria cobrar o
        # mesmo período duas vezes. Recusa antes de cancelar.
        _exigir(db, titulo_bancario.LIBERAR_COMPETENCIA, [billing.id])
    try:
        originals = ensure_substitution_allows_removal(
            db, billing, revert_substitution=payload.reverter_substituicao,
        )
    except BillingSubstitutionError as exc:
        raise _substitution_conflict(exc) from exc

    try:
        if originals:
            restore_substituted_originals(
                db, billing, user_id=current_user.id, reason=payload.reason,
            )
        lock_charge_items_for_billings(db, [billing])
        billing.status = BillingStatus.CANCELED
        nota_extra = ''
        if titulo.ativo_no_banco:
            nota_extra = (
                f'\n[ATENÇÃO] Boleto Ailos (nosso número {titulo.nosso_numero or "—"}) '
                'segue ativo no banco — baixa manual pendente.'
            )
        billing.notes = f'{billing.notes or ""}\nCancelada: {payload.reason}{nota_extra}'.strip()
        titulo_bancario.marcar_baixa_pendente(db, [billing.id], f'cancelada: {payload.reason}')
        _encerrar_pendencia_resolvida_no_cancelamento(db, billing.id, current_user.id)
        if payload.liberar_competencia:
            release_billing_competencia(
                db, billing, user_id=current_user.id, justification=payload.reason,
            )
    except (BillingSubstitutionError, titulo_bancario.PoliticaBancariaError) as exc:
        db.rollback()
        raise _substitution_conflict(exc) from exc
    refresh_charge_items_for_billing(db, billing, commit=False)
    _commit_billing_write(db)
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
        _exigir(db, titulo_bancario.ALTERAR_VALOR, [billing.id])

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
def delete_item(
    item_id: int,
    reverter_substituicao: bool = Query(
        default=False,
        description='Obrigatório para remover boleto único/negociação: reabre as cobranças que ele substituiu.',
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL)),
):
    obj = _lock_billing_or_404(db, item_id)
    if obj.status == BillingStatus.PAID:
        raise HTTPException(
            status_code=400,
            detail='Cobrança paga não pode ser removida; preserve o histórico financeiro.',
        )
    # Cobrança com qualquer histórico bancário (registrada, baixada, em
    # registro, desfecho desconhecido, remessa CNAB) não é removida: remover
    # tirava o título da conciliação e liberava o mês para nova cobrança
    # enquanto o boleto seguia pagável (FIN-02). O caminho é cancelar.
    _exigir(db, titulo_bancario.EXCLUIR, [obj.id])
    restored: list[int] = []
    try:
        originals = ensure_substitution_allows_removal(
            db, obj, revert_substitution=reverter_substituicao,
        )
        if originals:
            restored = restore_substituted_originals(
                db, obj, user_id=current_user.id, reason=f'cobrança #{obj.id} removida',
            )
    except BillingSubstitutionError as exc:
        db.rollback()
        raise _substitution_conflict(exc) from exc
    lock_charge_items_for_billings(db, [obj])
    obj.is_deleted = True
    refresh_charge_items_for_billing(db, obj, commit=False)
    _commit_billing_write(db)
    return {'message': 'Cobrança removida com sucesso', 'reabertas': restored}


@router.post('/{item_id}/liberar-competencia', response_model=BillingOut)
def release_period(
    item_id: int,
    payload: BillingReleasePeriod,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL)),
):
    """Devolve o mês de uma mensalidade cancelada para nova cobrança (carnê ou
    fechamento). Cancelada continua ocupando o mês até isto ser feito."""
    billing = _lock_billing_or_404(db, item_id)
    try:
        release_billing_competencia(
            db, billing, user_id=current_user.id, justification=payload.justificativa.strip(),
        )
    except (BillingSubstitutionError, titulo_bancario.PoliticaBancariaError) as exc:
        db.rollback()
        raise _substitution_conflict(exc) from exc
    _commit_billing_write(db)
    db.refresh(billing)
    row = base_query(db).filter(Billing.id == billing.id).first()
    return serialize_billing(row)
