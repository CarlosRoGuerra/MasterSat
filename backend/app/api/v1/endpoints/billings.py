from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime
from io import BytesIO, StringIO

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.billing import Billing
from app.models.billing_change_log import BillingChangeLog
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus, UserRole
from app.models.plan import Plan
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.models.user import User
from app.schemas.billing import (
    BillingCancel,
    BillingChangeLogOut,
    BillingCreate,
    BillingOut,
    BillingReceive,
    BillingUpdate,
    DelinquentClientItem,
    FinancialSummary,
    RevenueReportItem,
)
from app.services.financial import (
    decimal_to_float,
    generate_receipt_number,
    marcar_billing_pago,
    mark_charge_item_if_settled,
    period_bucket,
    refresh_overdue_statuses,
)

router = APIRouter()


def base_query(db: Session):
    refresh_overdue_statuses(db)
    return (
        db.query(Billing, Client.name.label('client_name'), Plan.name.label('plan_name'), Contract.status.label('contract_status'), Vehicle.plate.label('vehicle_plate'), Tracker.imei.label('tracker_identifier'))
        .join(Client, Client.id == Billing.client_id)
        .outerjoin(Contract, Contract.id == Billing.contract_id)
        .outerjoin(Plan, Plan.id == Contract.plan_id)
        .outerjoin(Vehicle, Vehicle.id == Billing.vehicle_id)
        .outerjoin(Tracker, Tracker.id == Billing.tracker_id)
        .filter(
            Billing.is_deleted.is_(False),
            Client.is_deleted.is_(False),
            or_(Contract.id.is_(None), Contract.is_deleted.is_(False)),
            or_(Plan.id.is_(None), Plan.is_deleted.is_(False)),
            or_(Vehicle.id.is_(None), Vehicle.is_deleted.is_(False)),
            or_(Tracker.id.is_(None), Tracker.is_deleted.is_(False)),
        )
    )


def serialize_billing(row) -> BillingOut:
    billing, client_name, plan_name, contract_status, vehicle_plate, tracker_identifier = row
    overdue_days = 0
    if billing.status == BillingStatus.OVERDUE:
        overdue_days = max((date.today() - billing.due_date).days, 0)
    return BillingOut(
        id=billing.id,
        contract_id=billing.contract_id,
        client_id=billing.client_id,
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
        vehicle_plate=vehicle_plate,
        tracker_identifier=tracker_identifier,
        plan_name=plan_name,
        contract_status=contract_status,
        overdue_days=overdue_days,
    )


def apply_filters(query, search: str | None, status: str | None, client_id: int | None, contract_id: int | None, due_from: date | None, due_to: date | None, vehicle_id: int | None = None):
    if search:
        query = query.filter(or_(Client.name.ilike(f'%{search}%'), Client.cpf_cnpj.ilike(f'%{search}%'), Billing.receipt_number.ilike(f'%{search}%'), Billing.notes.ilike(f'%{search}%'), Billing.title.ilike(f'%{search}%')))
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
        .join(Billing, Billing.client_id == Client.id)
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
    writer.writerow(['ID', 'Cliente', 'Veículo', 'Rastreador', 'Título', 'Tipo', 'Valor', 'Vencimento', 'Status', 'Recebido em', 'Recibo'])
    for row in rows:
        item = serialize_billing(row)
        writer.writerow([item.id, item.client_name, item.vehicle_plate or '', item.tracker_identifier or '', item.title or item.plan_name or '', item.billing_type, item.amount, item.due_date, item.status, item.payment_date or '', item.receipt_number or ''])
    buffer.seek(0)
    return StreamingResponse(iter([buffer.getvalue()]), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename=financeiro.csv'})


@router.get('/exports/xlsx')
def export_xlsx(search: str | None = None, status: str | None = None, client_id: int | None = None, contract_id: int | None = None, due_from: date | None = None, due_to: date | None = None, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    rows = apply_filters(base_query(db), search, status, client_id, contract_id, due_from, due_to).order_by(Billing.due_date.desc()).all()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Financeiro'
    sheet.append(['ID', 'Cliente', 'Título', 'Tipo', 'Valor', 'Vencimento', 'Status', 'Recebido em', 'Recibo'])
    for row in rows:
        item = serialize_billing(row)
        sheet.append([item.id, item.client_name, item.vehicle_plate or '', item.tracker_identifier or '', item.title or item.plan_name or '', item.billing_type, item.amount, str(item.due_date), item.status, str(item.payment_date or ''), item.receipt_number or ''])
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename=financeiro.xlsx'})


def _receipt_pdf(billing: Billing, client_name: str | None) -> BytesIO:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(f'Recibo {billing.receipt_number or billing.id}')
    y = 800
    pdf.setFont('Helvetica-Bold', 18)
    pdf.drawString(50, y, 'Recibo de Pagamento')
    y -= 40
    pdf.setFont('Helvetica', 11)
    lines = [
        f'Recibo: {billing.receipt_number or "-"}',
        f'Cliente: {client_name or billing.client_id}',
        f'Título: {billing.title or billing.notes or "Cobrança"}',
        f'Valor: R$ {decimal_to_float(billing.paid_amount or billing.amount):.2f}',
        f'Data de pagamento: {billing.payment_date.strftime("%d/%m/%Y") if billing.payment_date else "-"}',
        f'Forma de pagamento: {billing.payment_method or "-"}',
    ]
    for line in lines:
        pdf.drawString(50, y, line)
        y -= 24
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer


@router.get('/{item_id}/receipt')
def download_receipt(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    row = base_query(db).filter(Billing.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail='Cobrança não encontrada')
    # base_query retorna 6 colunas; só as duas primeiras interessam aqui
    billing, client_name, *_ = row
    if billing.status != BillingStatus.PAID:
        raise HTTPException(status_code=400, detail='Recibo disponível apenas para cobranças pagas')
    buffer = _receipt_pdf(billing, client_name)
    filename = f'recibo-{billing.receipt_number or item_id}.pdf'
    return StreamingResponse(buffer, media_type='application/pdf', headers={'Content-Disposition': f'inline; filename={filename}'})


@router.get('/', response_model=list[BillingOut])
def list_items(search: str | None = None, status: str | None = None, client_id: int | None = None, contract_id: int | None = None, vehicle_id: int | None = None, due_from: date | None = None, due_to: date | None = None, limit: int = Query(default=200, ge=1, le=1000), db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    query = apply_filters(base_query(db), search, status, client_id, contract_id, due_from, due_to, vehicle_id).order_by(Billing.due_date.desc(), Billing.id.desc())
    return [serialize_billing(row) for row in query.limit(limit).all()]


@router.post('/', response_model=BillingOut)
def create_item(payload: BillingCreate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    client = db.get(Client, payload.client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail='Cliente não encontrado')
    if payload.contract_id:
        contract = db.get(Contract, payload.contract_id)
        if not contract or contract.is_deleted:
            raise HTTPException(status_code=404, detail='Contrato não encontrado')
    data = payload.model_dump()
    if not data.get('period_label'):
        data['period_label'] = payload.due_date.strftime('%m/%Y')
    obj = Billing(**data)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    if obj.status == BillingStatus.PAID:
        obj.receipt_number = obj.receipt_number or generate_receipt_number(obj.id)
        obj.paid_amount = obj.paid_amount or obj.amount
        db.commit()
        db.refresh(obj)
        mark_charge_item_if_settled(db, obj.item_id)
    row = base_query(db).filter(Billing.id == obj.id).first()
    return serialize_billing(row)


@router.get('/{item_id}', response_model=BillingOut)
def get_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    row = base_query(db).filter(Billing.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail='Cobrança não encontrada')
    return serialize_billing(row)


@router.post('/{item_id}/receive', response_model=BillingOut)
def receive_billing(item_id: int, payload: BillingReceive, db: Session = Depends(get_db), current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    billing = db.get(Billing, item_id)
    if not billing or billing.is_deleted:
        raise HTTPException(status_code=404, detail='Cobrança não encontrada')
    if billing.status == BillingStatus.CANCELED:
        raise HTTPException(status_code=400, detail='Cobrança cancelada não pode ser recebida.')
    marcar_billing_pago(
        db, billing,
        payment_date=payload.payment_date,
        paid_amount=payload.paid_amount,
        payment_method=payload.payment_method,
        notes=payload.notes,
    )
    row = base_query(db).filter(Billing.id == billing.id).first()
    return serialize_billing(row)


@router.post('/{item_id}/cancel', response_model=BillingOut)
def cancel_billing(item_id: int, payload: BillingCancel, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    billing = db.get(Billing, item_id)
    if not billing or billing.is_deleted:
        raise HTTPException(status_code=404, detail='Cobrança não encontrada')
    billing.status = BillingStatus.CANCELED
    billing.notes = f'{billing.notes or ""}\nCancelada: {payload.reason}'.strip()
    db.commit()
    db.refresh(billing)
    mark_charge_item_if_settled(db, billing.item_id)
    row = base_query(db).filter(Billing.id == billing.id).first()
    return serialize_billing(row)


@router.put('/{item_id}', response_model=BillingOut)
def update_item(item_id: int, payload: BillingUpdate, db: Session = Depends(get_db), current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    billing = db.get(Billing, item_id)
    if not billing or billing.is_deleted:
        raise HTTPException(status_code=404, detail='Cobrança não encontrada')
    data = payload.model_dump(exclude_unset=True)
    justification = data.pop('justification', None)

    if ('amount' in data or 'due_date' in data) and not justification:
        raise HTTPException(status_code=400, detail='Justificativa é obrigatória para alterar valor ou vencimento.')

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
    if billing.status == BillingStatus.PAID:
        mark_charge_item_if_settled(db, billing.item_id)
    row = base_query(db).filter(Billing.id == billing.id).first()
    return serialize_billing(row)


@router.delete('/{item_id}')
def delete_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    obj = db.get(Billing, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Cobrança não encontrada')
    obj.is_deleted = True
    db.commit()
    return {'message': 'Cobrança removida com sucesso'}
