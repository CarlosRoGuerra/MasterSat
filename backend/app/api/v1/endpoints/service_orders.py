from __future__ import annotations

import logging
from datetime import datetime
from io import BytesIO
from secrets import token_urlsafe
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.config import settings
from app.core.integrity import raise_integrity_conflict
from app.core.security import create_file_access_token
from app.core.uploads import safe_object_name
from app.db.session import get_db
from app.models.client import Client
from app.models.document import Document
from app.models.enums import DocumentReviewStatus, OrderStatus, OrderType, UserRole
from app.models.service_order import ServiceOrder
from app.models.service_order_status_log import ServiceOrderStatusLog
from app.models.tracker import Tracker
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.document import DocumentDeleteOut, DocumentOut, DocumentReviewUpdate
from app.schemas.service_order import (
    ServiceOrderCreate,
    ServiceOrderOut,
    ServiceOrderPdfCreate,
    ServiceOrderStatusLogOut,
    ServiceOrderStatusUpdate,
    ServiceOrderUpdate,
)
from app.services.storage import remove_object, upload_bytes

router = APIRouter()
logger = logging.getLogger(__name__)

VIEW_ROLES = (UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL)
EDIT_ROLES = (UserRole.ADMIN, UserRole.OPERATIONAL)

_SERVICE_ORDER_INTEGRITY_MESSAGES = {
    'ix_service_orders_number': 'Já existe uma ordem de serviço com este número.',
}
_SERVICE_ORDER_SQLITE_CONSTRAINTS = {
    'UNIQUE constraint failed: service_orders.number': 'ix_service_orders_number',
}


def _build_document_urls(document_id: int) -> tuple[str, str]:
    base = f"{settings.backend_public_url.rstrip('/')}/{settings.api_v1_prefix.lstrip('/')}"
    token = create_file_access_token(document_id)
    return (
        f"{base}/documents/{document_id}/view?token={token}",
        f"{base}/documents/{document_id}/view?token={token}&download=1",
    )


def _document_to_out(document: Document) -> DocumentOut:
    view_url, download_url = _build_document_urls(document.id)
    return DocumentOut(
        id=document.id,
        file_name=document.file_name,
        category=document.category,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        review_status=document.review_status,
        review_notes=document.review_notes,
        url=view_url,
        download_url=download_url,
    )


def _base_query(db: Session):
    technician_alias = User
    return (
        db.query(
            ServiceOrder,
            Client.name.label('client_name'),
            Vehicle.plate.label('vehicle_plate'),
            Tracker.imei.label('tracker_imei'),
            User.name.label('technician_name'),
        )
        .join(Client, Client.id == ServiceOrder.client_id)
        .outerjoin(Vehicle, Vehicle.id == ServiceOrder.vehicle_id)
        .outerjoin(Tracker, Tracker.id == ServiceOrder.tracker_id)
        .outerjoin(User, User.id == ServiceOrder.technician_id)
        .filter(ServiceOrder.is_deleted.is_(False))
    )


def _serialize(row) -> ServiceOrderOut:
    order, client_name, vehicle_plate, tracker_imei, technician_name = row
    return ServiceOrderOut(
        id=order.id,
        number=order.number,
        type=order.type,
        status=order.status,
        client_id=order.client_id,
        vehicle_id=order.vehicle_id,
        tracker_id=order.tracker_id,
        technician_id=order.technician_id,
        scheduled_at=order.scheduled_at,
        executed_at=order.executed_at,
        checklist=order.checklist,
        observations=order.observations,
        client_name=client_name,
        vehicle_plate=vehicle_plate,
        tracker_label=tracker_imei,
        technician_name=technician_name,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


def _ensure_entities(db: Session, payload: dict):
    client = db.get(Client, payload.get('client_id')) if payload.get('client_id') else None
    if payload.get('client_id') and (not client or client.is_deleted):
        raise HTTPException(status_code=404, detail='Cliente não encontrado')
    vehicle = db.get(Vehicle, payload.get('vehicle_id')) if payload.get('vehicle_id') else None
    if payload.get('vehicle_id') and (not vehicle or vehicle.is_deleted):
        raise HTTPException(status_code=404, detail='Veículo não encontrado')
    tracker = db.get(Tracker, payload.get('tracker_id')) if payload.get('tracker_id') else None
    if payload.get('tracker_id') and (not tracker or tracker.is_deleted):
        raise HTTPException(status_code=404, detail='Rastreador não encontrado')
    technician = db.get(User, payload.get('technician_id')) if payload.get('technician_id') else None
    if payload.get('technician_id') and (not technician or technician.is_deleted):
        raise HTTPException(status_code=404, detail='Técnico responsável não encontrado')

    if vehicle and client and vehicle.client_id != client.id:
        raise HTTPException(status_code=400, detail='O veículo informado não pertence ao cliente selecionado')
    if tracker and vehicle and tracker.vehicle_id and tracker.vehicle_id != vehicle.id:
        raise HTTPException(status_code=400, detail='O rastreador informado não está vinculado ao veículo selecionado')



def _generate_order_number(db: Session) -> str:
    prefix = datetime.now().strftime('OS-%Y%m%d')
    total = db.query(func.count(ServiceOrder.id)).filter(ServiceOrder.number.like(f'{prefix}%')).scalar() or 0
    return f'{prefix}-{total + 1:03d}'


def _append_status_log(db: Session, order_id: int, previous_status: OrderStatus | None, new_status: OrderStatus, changed_by_id: int | None, notes: str | None = None):
    db.add(
        ServiceOrderStatusLog(
            service_order_id=order_id,
            previous_status=previous_status,
            new_status=new_status,
            changed_by_id=changed_by_id,
            notes=notes,
        )
    )


def _insert_service_order(db: Session, data: dict, *, number_generated: bool) -> ServiceOrder:
    """Insere a ordem de serviço.

    Quando o número foi gerado por nós (operador não informou), a contagem
    usada em `_generate_order_number` não é atômica com o INSERT: duas
    aberturas concorrentes no mesmo dia podem calcular a mesma sequência e
    colidir no UNIQUE de `service_orders.number`. Não é um conflito do
    operador — regeneramos com o estado atual do banco e tentamos mais uma
    vez. Um número informado manualmente e duplicado, por outro lado, é
    responsabilidade do operador e vira 409 já na primeira tentativa.
    """
    max_attempts = 2 if number_generated else 1
    for attempt in range(max_attempts):
        if number_generated and attempt > 0:
            data = {**data, 'number': _generate_order_number(db)}
        item = ServiceOrder(**data)
        if item.status == OrderStatus.COMPLETED and item.executed_at is None:
            item.executed_at = datetime.utcnow()
        db.add(item)
        try:
            db.flush()
            return item
        except IntegrityError as exc:
            db.rollback()
            if attempt == max_attempts - 1:
                raise_integrity_conflict(
                    db, exc, _SERVICE_ORDER_INTEGRITY_MESSAGES,
                    sqlite_columns=_SERVICE_ORDER_SQLITE_CONSTRAINTS,
                )


def _fetch_order_or_404(item_id: int, db: Session) -> ServiceOrder:
    item = db.get(ServiceOrder, item_id)
    if not item or item.is_deleted:
        raise HTTPException(status_code=404, detail='Ordem de serviço não encontrada')
    return item


def _order_context(db: Session, item: ServiceOrder):
    client = db.get(Client, item.client_id) if item.client_id else None
    vehicle = db.get(Vehicle, item.vehicle_id) if item.vehicle_id else None
    tracker = db.get(Tracker, item.tracker_id) if item.tracker_id else None
    technician = db.get(User, item.technician_id) if item.technician_id else None
    return client, vehicle, tracker, technician


def _draw_title(pdf: canvas.Canvas, title: str, subtitle: str | None = None):
    y = 800
    pdf.setFont('Helvetica-Bold', 18)
    pdf.drawString(50, y, title)
    if subtitle:
        y -= 20
        pdf.setFont('Helvetica', 10)
        pdf.drawString(50, y, subtitle)
    return y - 30


def _pdf_lines_for_order(item: ServiceOrder, client: Client | None, vehicle: Vehicle | None, tracker: Tracker | None, technician: User | None):
    checklist_items = []
    if isinstance(item.checklist, dict):
        raw_items = item.checklist.get('items') or []
        if isinstance(raw_items, list):
            checklist_items = [str(i) for i in raw_items if str(i).strip()]
    return [
        f'Ordem: {item.number}',
        f'Tipo: {item.type.value}',
        f'Status: {item.status.value}',
        f'Cliente: {client.name if client else "-"}',
        f'Veículo: {vehicle.plate if vehicle else "-"}',
        f'Rastreador: {tracker.imei if tracker else "-"}',
        f'Técnico: {technician.name if technician else "-"}',
        f'Agendamento: {item.scheduled_at.strftime("%d/%m/%Y %H:%M") if item.scheduled_at else "-"}',
        f'Execução: {item.executed_at.strftime("%d/%m/%Y %H:%M") if item.executed_at else "-"}',
        'Checklist: ' + (', '.join(checklist_items) if checklist_items else 'Não informado'),
        'Observações: ' + (item.observations or 'Sem observações'),
    ]


def _build_pdf_bytes(kind: str, item: ServiceOrder, client: Client | None, vehicle: Vehicle | None, tracker: Tracker | None, technician: User | None, logs: list[ServiceOrderStatusLog] | None = None) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(f'{kind}-{item.number}')
    subtitle = 'Documento operacional gerado automaticamente pelo sistema'
    title_map = {
        'ordem_servico': 'Ordem de Serviço',
        'termo_instalacao': 'Termo de Instalação',
        'termo_retirada': 'Termo de Retirada de Equipamento',
        'historico_execucao': 'Histórico de Execução',
    }
    y = _draw_title(pdf, title_map.get(kind, 'Documento Operacional'), subtitle)
    pdf.setFont('Helvetica', 11)
    if kind == 'historico_execucao' and logs is not None:
        lines = [f'Ordem: {item.number}', f'Cliente: {client.name if client else "-"}', '']
        for entry in logs:
            lines.append(
                f"{entry.created_at.strftime('%d/%m/%Y %H:%M') if entry.created_at else '-'} • {entry.previous_status.value if entry.previous_status else 'novo'} → {entry.new_status.value} • {entry.notes or 'sem observações'}"
            )
    else:
        lines = _pdf_lines_for_order(item, client, vehicle, tracker, technician)
    for line in lines:
        for part in [line[i:i+100] for i in range(0, len(line), 100)] or ['']:
            pdf.drawString(50, y, part)
            y -= 18
            if y < 60:
                pdf.showPage()
                y = 800
                pdf.setFont('Helvetica', 11)
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()


@router.get('/', response_model=list[ServiceOrderOut])
def list_items(
    search: str | None = None,
    client_id: int | None = None,
    vehicle_id: int | None = None,
    technician_id: int | None = None,
    status: OrderStatus | None = None,
    type: OrderType | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*VIEW_ROLES)),
):
    query = _base_query(db)
    if search:
        term = f'%{search.strip()}%'
        query = query.filter(
            or_(
                ServiceOrder.number.ilike(term),
                Client.name.ilike(term),
                Vehicle.plate.ilike(term),
                Tracker.imei.ilike(term),
                ServiceOrder.observations.ilike(term),
            )
        )
    if client_id:
        query = query.filter(ServiceOrder.client_id == client_id)
    if vehicle_id:
        query = query.filter(ServiceOrder.vehicle_id == vehicle_id)
    if technician_id:
        query = query.filter(ServiceOrder.technician_id == technician_id)
    if status:
        query = query.filter(ServiceOrder.status == status)
    if type:
        query = query.filter(ServiceOrder.type == type)
    rows = query.order_by(ServiceOrder.created_at.desc(), ServiceOrder.id.desc()).limit(limit).all()
    return [_serialize(row) for row in rows]


@router.post('/', response_model=ServiceOrderOut)
def create_item(
    payload: ServiceOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EDIT_ROLES)),
):
    data = payload.model_dump()
    _ensure_entities(db, data)
    number_generated = not data.get('number')
    if number_generated:
        data['number'] = _generate_order_number(db)
    item = _insert_service_order(db, data, number_generated=number_generated)
    _append_status_log(db, item.id, None, item.status, current_user.id, 'Abertura da ordem de serviço')
    db.commit()
    row = _base_query(db).filter(ServiceOrder.id == item.id).first()
    return _serialize(row)


@router.get('/{item_id}', response_model=ServiceOrderOut)
def get_item(item_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*VIEW_ROLES))):
    row = _base_query(db).filter(ServiceOrder.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail='Ordem de serviço não encontrada')
    return _serialize(row)


@router.put('/{item_id}', response_model=ServiceOrderOut)
def update_item(
    item_id: int,
    payload: ServiceOrderUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EDIT_ROLES)),
):
    item = _fetch_order_or_404(item_id, db)
    data = payload.model_dump(exclude_unset=True)
    _ensure_entities(db, {**item.__dict__, **data})
    previous_status = item.status
    for key, value in data.items():
        setattr(item, key, value)
    if item.status == OrderStatus.COMPLETED and item.executed_at is None:
        item.executed_at = datetime.utcnow()
    if 'status' in data and data['status'] != previous_status:
        _append_status_log(db, item.id, previous_status, item.status, current_user.id, data.get('observations'))
    db.commit()
    row = _base_query(db).filter(ServiceOrder.id == item.id).first()
    return _serialize(row)


@router.post('/{item_id}/status', response_model=ServiceOrderOut)
def update_status(
    item_id: int,
    payload: ServiceOrderStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EDIT_ROLES)),
):
    item = _fetch_order_or_404(item_id, db)
    previous_status = item.status
    item.status = payload.status
    if payload.notes:
        item.observations = f'{item.observations}\n\n{payload.notes}'.strip() if item.observations else payload.notes
    if payload.status == OrderStatus.COMPLETED and item.executed_at is None:
        item.executed_at = datetime.utcnow()
    _append_status_log(db, item.id, previous_status, item.status, current_user.id, payload.notes)
    db.commit()
    row = _base_query(db).filter(ServiceOrder.id == item.id).first()
    return _serialize(row)


@router.get('/{item_id}/logs', response_model=list[ServiceOrderStatusLogOut])
def list_logs(item_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*VIEW_ROLES))):
    _fetch_order_or_404(item_id, db)
    rows = (
        db.query(ServiceOrderStatusLog, User.name.label('changed_by_name'))
        .outerjoin(User, User.id == ServiceOrderStatusLog.changed_by_id)
        .filter(ServiceOrderStatusLog.service_order_id == item_id)
        .order_by(ServiceOrderStatusLog.created_at.desc(), ServiceOrderStatusLog.id.desc())
        .all()
    )
    return [
        ServiceOrderStatusLogOut(
            id=log.id,
            previous_status=log.previous_status,
            new_status=log.new_status,
            notes=log.notes,
            changed_by_id=log.changed_by_id,
            changed_by_name=changed_by_name,
            created_at=log.created_at,
        )
        for log, changed_by_name in rows
    ]


@router.delete('/{item_id}')
def delete_item(item_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*EDIT_ROLES))):
    item = _fetch_order_or_404(item_id, db)
    item.is_deleted = True
    db.commit()
    return {'message': 'Ordem de serviço removida com soft delete'}


@router.get('/{item_id}/documents', response_model=list[DocumentOut])
def list_documents(item_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*VIEW_ROLES))):
    _fetch_order_or_404(item_id, db)
    docs = db.scalars(
        select(Document)
        .where(
            Document.reference_type == 'service_order',
            Document.reference_id == item_id,
            Document.active.is_(True),
        )
        .order_by(Document.id.desc())
    ).all()
    return [_document_to_out(doc) for doc in docs]


@router.post('/{item_id}/documents', response_model=list[DocumentOut])
def upload_documents(
    item_id: int,
    category: str = Form(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*EDIT_ROLES)),
):
    _fetch_order_or_404(item_id, db)
    created: list[DocumentOut] = []
    for file in files:
        content = file.file.read()
        if not content:
            continue
        object_key = f'service-orders/{item_id}/documents/{uuid4()}-{safe_object_name(file.filename)}'
        upload_bytes(object_key, content, file.content_type or 'application/octet-stream')
        doc = Document(
            file_name=file.filename,
            object_key=object_key,
            content_type=file.content_type or 'application/octet-stream',
            size_bytes=len(content),
            reference_type='service_order',
            reference_id=item_id,
            category=category,
            review_status=DocumentReviewStatus.SUBMITTED,
        )
        db.add(doc)
        db.flush()
        created.append(_document_to_out(doc))
    if not created:
        raise HTTPException(status_code=400, detail='Nenhum arquivo válido foi enviado')
    db.commit()
    return created


@router.put('/{item_id}/documents/{document_id}/review', response_model=DocumentOut)
def review_document(
    item_id: int,
    document_id: int,
    payload: DocumentReviewUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*EDIT_ROLES)),
):
    _fetch_order_or_404(item_id, db)
    doc = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.reference_type == 'service_order',
            Document.reference_id == item_id,
            Document.active.is_(True),
        )
    )
    if not doc:
        raise HTTPException(status_code=404, detail='Documento não encontrado')
    doc.review_status = payload.review_status
    doc.review_notes = payload.review_notes
    db.commit()
    db.refresh(doc)
    return _document_to_out(doc)


@router.delete('/{item_id}/documents/{document_id}', response_model=DocumentDeleteOut)
def delete_document(item_id: int, document_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*EDIT_ROLES))):
    _fetch_order_or_404(item_id, db)
    doc = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.reference_type == 'service_order',
            Document.reference_id == item_id,
            Document.active.is_(True),
        )
    )
    if not doc:
        raise HTTPException(status_code=404, detail='Documento não encontrado')
    try:
        remove_object(doc.object_key)
    except Exception:  # noqa: BLE001 — exclusão do registro não pode depender do storage
        logger.warning('Falha ao remover objeto %s do storage', doc.object_key, exc_info=True)
    doc.active = False
    db.commit()
    return DocumentDeleteOut(message='Documento removido com sucesso')


@router.post('/{item_id}/generate-document', response_model=DocumentOut)
def generate_document(
    item_id: int,
    payload: ServiceOrderPdfCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EDIT_ROLES)),
):
    item = _fetch_order_or_404(item_id, db)
    client, vehicle, tracker, technician = _order_context(db, item)
    logs = None
    if payload.kind == 'historico_execucao':
        logs = db.scalars(select(ServiceOrderStatusLog).where(ServiceOrderStatusLog.service_order_id == item_id).order_by(ServiceOrderStatusLog.created_at.asc())).all()
    pdf_bytes = _build_pdf_bytes(payload.kind, item, client, vehicle, tracker, technician, logs)
    title_map = {
        'ordem_servico': 'ordem-servico',
        'termo_instalacao': 'termo-instalacao',
        'termo_retirada': 'termo-retirada',
        'historico_execucao': 'historico-execucao',
    }
    filename = f"{title_map.get(payload.kind, payload.kind)}-{item.number}.pdf"
    object_key = f'service-orders/{item_id}/generated/{uuid4()}-{filename}'
    upload_bytes(object_key, pdf_bytes, 'application/pdf')
    document = Document(
        file_name=filename,
        object_key=object_key,
        content_type='application/pdf',
        size_bytes=len(pdf_bytes),
        reference_type='service_order',
        reference_id=item_id,
        category=payload.kind,
        review_status=DocumentReviewStatus.APPROVED,
        review_notes=f'Gerado automaticamente por {current_user.name}',
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return _document_to_out(document)


@router.get('/{item_id}/pdf/{kind}')
def stream_generated_pdf(kind: str, item_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*VIEW_ROLES))):
    if kind not in {'ordem_servico', 'termo_instalacao', 'termo_retirada', 'historico_execucao'}:
        raise HTTPException(status_code=400, detail='Tipo de PDF inválido')
    item = _fetch_order_or_404(item_id, db)
    client, vehicle, tracker, technician = _order_context(db, item)
    logs = None
    if kind == 'historico_execucao':
        logs = db.scalars(select(ServiceOrderStatusLog).where(ServiceOrderStatusLog.service_order_id == item_id).order_by(ServiceOrderStatusLog.created_at.asc())).all()
    content = _build_pdf_bytes(kind, item, client, vehicle, tracker, technician, logs)
    return StreamingResponse(BytesIO(content), media_type='application/pdf', headers={'Content-Disposition': f'inline; filename={kind}-{item.number}.pdf'})
