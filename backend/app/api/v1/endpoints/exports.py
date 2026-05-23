"""
Exportação de dados em CSV e Excel.

Endpoints:
  GET /exports/clients    → Lista de clientes
  GET /exports/vehicles   → Lista de veículos
  GET /exports/trackers   → Lista de rastreadores
  GET /exports/billings   → Cobranças com filtros
  GET /exports/delinquents → Relatório de inadimplentes
"""
from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.limiter import limiter

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus, UserRole
from app.models.plan import Plan
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle

router = APIRouter()

VIEW_ROLES = (UserRole.ADMIN, UserRole.FINANCIAL, UserRole.OPERATIONAL)


def _csv_response(headers: list[str], rows: list[list], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=';', quotechar='"', quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue().encode('utf-8-sig')]),  # utf-8-sig = BOM para Excel abrir correto
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


def _excel_response(headers: list[str], rows: list[list], filename: str) -> StreamingResponse:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = 'Dados'

    # Cabeçalho com estilo
    header_fill = PatternFill('solid', fgColor='1E3A5F')
    header_font = Font(color='FFFFFF', bold=True)
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')

    for r, row in enumerate(rows, 2):
        for c, val in enumerate(row, 1):
            ws.cell(row=r, column=c, value=val)

    # Auto-ajusta largura das colunas
    for col in ws.columns:
        max_len = max((len(str(cell.value or '')) for cell in col), default=0)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 50)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


def _build_response(fmt: str, headers: list[str], rows: list[list], name: str) -> StreamingResponse:
    if fmt == 'xlsx':
        return _excel_response(headers, rows, f'{name}.xlsx')
    return _csv_response(headers, rows, f'{name}.csv')


# ---------------------------------------------------------------------------
# Exportar clientes
# ---------------------------------------------------------------------------

@router.get('/clients')
@limiter.limit('10/minute')
def export_clients(
    request: Request,
    fmt: str = Query(default='csv', pattern='^(csv|xlsx)$'),
    status: str | None = None,
    type: str | None = None,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    query = db.query(Client).filter(Client.is_deleted.is_(False))
    if status:
        query = query.filter(Client.status == status)
    if type:
        query = query.filter(Client.type == type)
    clients = query.order_by(Client.name).all()

    headers = ['ID', 'Nome', 'CPF/CNPJ', 'Tipo', 'Status', 'E-mail', 'Telefone',
               'Cidade', 'UF', 'Dia Vencimento', 'Data Cadastro']
    rows = [
        [
            c.id, c.name, c.cpf_cnpj, c.type.upper(),
            c.status.value if hasattr(c.status, 'value') else str(c.status),
            c.email or '', c.phone or '', c.city or '', c.state or '',
            c.billing_day or '',
            c.created_at.strftime('%d/%m/%Y') if c.created_at else '',
        ]
        for c in clients
    ]
    return _build_response(fmt, headers, rows, 'clientes')


# ---------------------------------------------------------------------------
# Exportar veículos
# ---------------------------------------------------------------------------

@router.get('/vehicles')
def export_vehicles(
    fmt: str = Query(default='csv', pattern='^(csv|xlsx)$'),
    status: str | None = None,
    client_id: int | None = None,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    query = db.query(Vehicle).filter(Vehicle.is_deleted.is_(False))
    if status:
        query = query.filter(Vehicle.status == status)
    if client_id:
        query = query.filter(Vehicle.client_id == client_id)
    vehicles = query.order_by(Vehicle.plate).all()

    client_map = {c.id: c.name for c in db.query(Client).filter(Client.is_deleted.is_(False)).all()}
    tracker_map: dict[int, Tracker] = {
        t.vehicle_id: t
        for t in db.query(Tracker).filter(
            Tracker.is_deleted.is_(False),
            Tracker.vehicle_id.isnot(None),
        ).all()
        if t.vehicle_id
    }

    headers = ['ID', 'Placa', 'Marca', 'Modelo', 'Ano Modelo', 'Tipo', 'Cor',
               'Chassi', 'Renavam', 'Status', 'Cliente', 'IMEI Rastreador', 'Data Cadastro']
    rows = [
        [
            v.id, v.plate, v.brand or '', v.model or '', v.model_year or '',
            v.type or '',v.color or '', v.chassis or '', v.renavam or '',
            v.status.value if hasattr(v.status, 'value') else str(v.status),
            client_map.get(v.client_id, ''),
            tracker_map[v.id].imei if v.id in tracker_map else '',
            v.created_at.strftime('%d/%m/%Y') if v.created_at else '',
        ]
        for v in vehicles
    ]
    return _build_response(fmt, headers, rows, 'veiculos')


# ---------------------------------------------------------------------------
# Exportar rastreadores
# ---------------------------------------------------------------------------

@router.get('/trackers')
def export_trackers(
    fmt: str = Query(default='csv', pattern='^(csv|xlsx)$'),
    status: str | None = None,
    client_id: int | None = None,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    query = db.query(Tracker).filter(Tracker.is_deleted.is_(False))
    if status:
        query = query.filter(Tracker.status == status)
    if client_id:
        query = query.filter(Tracker.client_id == client_id)
    trackers = query.order_by(Tracker.imei).all()

    client_map = {c.id: c.name for c in db.query(Client).filter(Client.is_deleted.is_(False)).all()}
    vehicle_map = {v.id: v.plate for v in db.query(Vehicle).filter(Vehicle.is_deleted.is_(False)).all()}

    headers = ['ID', 'IMEI', 'Marca', 'Modelo', 'Status', 'Cliente', 'Veículo (Placa)',
               'SIM (MSISDN)', 'ICCID', 'Operadora', 'Data Instalação', 'Garantia Até']
    rows = [
        [
            t.id, t.imei, t.brand or '', t.model or '',
            t.status.value if hasattr(t.status, 'value') else str(t.status),
            client_map.get(t.client_id, '') if t.client_id else '',
            vehicle_map.get(t.vehicle_id, '') if t.vehicle_id else '',
            t.sim_number or '', t.sim_iccid or '', t.carrier or '',
            t.install_date.strftime('%d/%m/%Y') if t.install_date else '',
            t.warranty_until.strftime('%d/%m/%Y') if t.warranty_until else '',
        ]
        for t in trackers
    ]
    return _build_response(fmt, headers, rows, 'rastreadores')


# ---------------------------------------------------------------------------
# Exportar cobranças
# ---------------------------------------------------------------------------

@router.get('/billings')
def export_billings(
    fmt: str = Query(default='csv', pattern='^(csv|xlsx)$'),
    status: str | None = None,
    client_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    query = db.query(Billing).filter(Billing.is_deleted.is_(False))
    if status:
        query = query.filter(Billing.status == status)
    if client_id:
        query = query.filter(Billing.client_id == client_id)
    if date_from:
        query = query.filter(Billing.due_date >= date_from)
    if date_to:
        query = query.filter(Billing.due_date <= date_to)
    billings = query.order_by(Billing.due_date.desc()).limit(5000).all()

    client_map = {c.id: c.name for c in db.query(Client).filter(Client.is_deleted.is_(False)).all()}
    vehicle_map = {v.id: v.plate for v in db.query(Vehicle).filter(Vehicle.is_deleted.is_(False)).all()}

    headers = ['ID', 'Cliente', 'Veículo', 'Título', 'Tipo', 'Vencimento', 'Valor',
               'Valor Pago', 'Status', 'Data Pagamento', 'Forma Pagamento',
               'Parcela', 'Total Parcelas', 'Período', 'Nº Recibo']
    rows = [
        [
            b.id,
            client_map.get(b.client_id, ''),
            vehicle_map.get(b.vehicle_id, '') if b.vehicle_id else '',
            b.title or '',
            b.billing_type or '',
            b.due_date.strftime('%d/%m/%Y') if b.due_date else '',
            float(b.amount),
            float(b.paid_amount) if b.paid_amount else 0.0,
            b.status.value if hasattr(b.status, 'value') else str(b.status),
            b.payment_date.strftime('%d/%m/%Y') if b.payment_date else '',
            b.payment_method or '',
            b.installment_number or 1,
            b.installment_total or 1,
            b.period_label or '',
            b.receipt_number or '',
        ]
        for b in billings
    ]
    return _build_response(fmt, headers, rows, 'cobrancas')


# ---------------------------------------------------------------------------
# Exportar relatório de inadimplentes
# ---------------------------------------------------------------------------

@router.get('/delinquents')
def export_delinquents(
    fmt: str = Query(default='csv', pattern='^(csv|xlsx)$'),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    from sqlalchemy import func

    results = (
        db.query(
            Client.id,
            Client.name,
            Client.cpf_cnpj,
            Client.email,
            Client.phone,
            func.count(Billing.id).label('total_vencidas'),
            func.sum(Billing.amount).label('total_valor'),
            func.min(Billing.due_date).label('vencimento_mais_antigo'),
        )
        .join(Billing, Billing.client_id == Client.id)
        .filter(
            Client.is_deleted.is_(False),
            Billing.is_deleted.is_(False),
            Billing.status == BillingStatus.OVERDUE,
        )
        .group_by(Client.id, Client.name, Client.cpf_cnpj, Client.email, Client.phone)
        .order_by(func.sum(Billing.amount).desc())
        .all()
    )

    headers = ['ID', 'Nome', 'CPF/CNPJ', 'E-mail', 'Telefone',
               'Qtd. Cobranças Vencidas', 'Valor Total em Aberto', 'Vencimento Mais Antigo']
    rows = [
        [
            r.id, r.name, r.cpf_cnpj, r.email or '', r.phone or '',
            r.total_vencidas,
            float(r.total_valor or 0),
            r.vencimento_mais_antigo.strftime('%d/%m/%Y') if r.vencimento_mais_antigo else '',
        ]
        for r in results
    ]
    return _build_response(fmt, headers, rows, 'inadimplentes')
