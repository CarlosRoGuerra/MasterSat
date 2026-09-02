from __future__ import annotations

import logging
from datetime import datetime
from io import BytesIO
from secrets import token_urlsafe
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.config import settings
from app.core.integrity import raise_integrity_conflict
from app.core.security import create_file_access_token
from app.core.uploads import read_limited, safe_object_name, validate_content_type
from app.db.session import get_db
from app.models.client import Client
from app.models.document import Document
from app.models.enums import DocumentReviewStatus, OrderStatus, OrderType, UserRole
from app.models.service_order import ServiceOrder
from app.models.service_order_material import ServiceOrderMaterial
from app.models.service_order_status_log import ServiceOrderStatusLog
from app.models.service_product import ServiceProduct
from app.models.tracker import Tracker
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.document import DocumentDeleteOut, DocumentOut, DocumentReviewUpdate
from app.schemas.service_order import (
    ServiceOrderCreate,
    ServiceOrderMaterialIn,
    ServiceOrderMaterialOut,
    ServiceOrderOut,
    ServiceOrderPdfCreate,
    ServiceOrderStatusLogOut,
    ServiceOrderStatusUpdate,
    ServiceOrderUpdate,
    SignatureIn,
)
from app.services.service_order_docx import decode_signature_image, gerar_os_docx, montar_dados_os
from app.services.service_order_pdf import gerar_os_pdf
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
        priority=order.priority,
        client_id=order.client_id,
        vehicle_id=order.vehicle_id,
        tracker_id=order.tracker_id,
        technician_id=order.technician_id,
        scheduled_at=order.scheduled_at,
        executed_at=order.executed_at,
        checklist=order.checklist,
        observations=order.observations,
        problem_description=order.problem_description,
        execution_description=order.execution_description,
        technician_signed_at=order.technician_signed_at,
        client_signed_at=order.client_signed_at,
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


_DOCUMENT_KINDS = {
    'ordem_servico': 'ordem-servico',
    'termo_instalacao': 'termo-instalacao',
    'termo_retirada': 'termo-retirada',
    'historico_execucao': 'historico-execucao',
}


def _ensure_completion_requirements(item: ServiceOrder) -> None:
    """OS 'profissional': concluir exige o registro mínimo de campo — o que
    foi feito, e as duas assinaturas confirmando o atendimento."""
    faltando = []
    if not (item.execution_description and item.execution_description.strip()):
        faltando.append('descrição do serviço executado')
    if not item.technician_signed_at:
        faltando.append('assinatura do técnico')
    if not item.client_signed_at:
        faltando.append('assinatura do cliente')
    if faltando:
        raise HTTPException(
            status_code=422,
            detail=f'Para concluir a ordem de serviço, preencha: {", ".join(faltando)}.',
        )


def _validate_signature_bytes(content: bytes) -> None:
    if not content.startswith(b'\x89PNG\r\n\x1a\n'):
        raise HTTPException(status_code=415, detail='A assinatura precisa ser uma imagem PNG.')
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail='Assinatura excede o tamanho máximo permitido.')


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
    if item.status == OrderStatus.COMPLETED and previous_status != OrderStatus.COMPLETED:
        _ensure_completion_requirements(item)
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
    if payload.status == OrderStatus.COMPLETED and previous_status != OrderStatus.COMPLETED:
        _ensure_completion_requirements(item)
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
def list_documents(item_id: int, include_inactive: bool = False, db: Session = Depends(get_db), _: User = Depends(require_roles(*VIEW_ROLES))):
    _fetch_order_or_404(item_id, db)
    query = select(Document).where(
        Document.reference_type == 'service_order',
        Document.reference_id == item_id,
    )
    if not include_inactive:
        query = query.where(Document.active.is_(True))
    docs = db.scalars(query.order_by(Document.id.desc())).all()
    return [_document_to_out(doc) for doc in docs]


@router.post('/{item_id}/documents', response_model=list[DocumentOut])
async def upload_documents(
    item_id: int,
    category: str = Form(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EDIT_ROLES)),
):
    _fetch_order_or_404(item_id, db)
    created: list[DocumentOut] = []
    for file in files:
        content_type = validate_content_type(file)
        content = await read_limited(file)
        if not content:
            continue
        object_key = f'service-orders/{item_id}/documents/{uuid4()}-{safe_object_name(file.filename)}'
        upload_bytes(object_key, content, content_type)
        doc = Document(
            file_name=file.filename,
            object_key=object_key,
            content_type=content_type,
            size_bytes=len(content),
            reference_type='service_order',
            reference_id=item_id,
            category=category,
            review_status=DocumentReviewStatus.SUBMITTED,
            uploaded_by_user_id=current_user.id,
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
    data = montar_dados_os(payload.kind, item, db)

    if payload.format == 'docx':
        content = gerar_os_docx(data)
        content_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        extension = 'docx'
    else:
        content = gerar_os_pdf(data)
        content_type = 'application/pdf'
        extension = 'pdf'

    filename = f"{_DOCUMENT_KINDS.get(payload.kind, payload.kind)}-{item.number}.{extension}"
    object_key = f'service-orders/{item_id}/generated/{uuid4()}-{filename}'
    upload_bytes(object_key, content, content_type)

    # Versionamento: o Document ativo mais recente do mesmo kind E FORMATO
    # vira "anterior" (active=False, preservado no storage) em vez de sumir.
    # Precisa ser por kind+formato, não só kind: PDF e DOCX da mesma "Ordem
    # de Serviço" são dois arquivos que o usuário quer manter disponíveis ao
    # mesmo tempo (um pra assinar/imprimir, outro editável) — não uma versão
    # substituindo a outra. Só regenerar o MESMO formato supersede o anterior.
    previous = db.scalar(
        select(Document)
        .where(
            Document.reference_type == 'service_order',
            Document.reference_id == item_id,
            Document.category == payload.kind,
            Document.content_type == content_type,
            Document.active.is_(True),
        )
        .order_by(Document.version.desc())
    )
    version = previous.version + 1 if previous else 1
    if previous:
        previous.active = False

    document = Document(
        file_name=filename,
        object_key=object_key,
        content_type=content_type,
        size_bytes=len(content),
        reference_type='service_order',
        reference_id=item_id,
        category=payload.kind,
        review_status=DocumentReviewStatus.APPROVED,
        review_notes=f'Gerado automaticamente por {current_user.name}',
        uploaded_by_user_id=current_user.id,
        version=version,
        supersedes_document_id=previous.id if previous else None,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return _document_to_out(document)


@router.get('/{item_id}/pdf/{kind}')
def stream_generated_pdf(kind: str, item_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*VIEW_ROLES))):
    if kind not in _DOCUMENT_KINDS:
        raise HTTPException(status_code=400, detail='Tipo de documento inválido')
    item = _fetch_order_or_404(item_id, db)
    data = montar_dados_os(kind, item, db)
    content = gerar_os_pdf(data)
    return StreamingResponse(BytesIO(content), media_type='application/pdf', headers={'Content-Disposition': f'inline; filename={kind}-{item.number}.pdf'})


@router.get('/{item_id}/docx/{kind}')
def stream_generated_docx(kind: str, item_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*VIEW_ROLES))):
    if kind not in _DOCUMENT_KINDS:
        raise HTTPException(status_code=400, detail='Tipo de documento inválido')
    item = _fetch_order_or_404(item_id, db)
    data = montar_dados_os(kind, item, db)
    content = gerar_os_docx(data)
    return StreamingResponse(
        BytesIO(content),
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        headers={'Content-Disposition': f'inline; filename={kind}-{item.number}.docx'},
    )


@router.post('/{item_id}/signature', response_model=ServiceOrderOut)
def upload_signature(
    item_id: int,
    payload: SignatureIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EDIT_ROLES)),
):
    item = _fetch_order_or_404(item_id, db)
    try:
        content = decode_signature_image(payload.image_base64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail='Assinatura inválida — envie um PNG em base64.') from exc
    _validate_signature_bytes(content)

    category = 'assinatura_tecnico' if payload.signer == 'technician' else 'assinatura_cliente'
    filename = f'{category}-{item.number}.png'
    object_key = f'service-orders/{item_id}/signatures/{uuid4()}-{filename}'
    upload_bytes(object_key, content, 'image/png')
    document = Document(
        file_name=filename,
        object_key=object_key,
        content_type='image/png',
        size_bytes=len(content),
        reference_type='service_order',
        reference_id=item_id,
        category=category,
        review_status=DocumentReviewStatus.APPROVED,
        uploaded_by_user_id=current_user.id,
    )
    db.add(document)
    db.flush()

    now = datetime.utcnow()
    if payload.signer == 'technician':
        item.technician_signature_document_id = document.id
        item.technician_signed_at = now
    else:
        item.client_signature_document_id = document.id
        item.client_signed_at = now

    db.commit()
    row = _base_query(db).filter(ServiceOrder.id == item.id).first()
    return _serialize(row)


# ── Materiais utilizados ────────────────────────────────────────────────

def _fetch_material_or_404(db: Session, item_id: int, material_id: int) -> ServiceOrderMaterial:
    material = db.scalar(
        select(ServiceOrderMaterial).where(
            ServiceOrderMaterial.id == material_id,
            ServiceOrderMaterial.service_order_id == item_id,
            ServiceOrderMaterial.is_deleted.is_(False),
        )
    )
    if not material:
        raise HTTPException(status_code=404, detail='Material não encontrado')
    return material


def _material_to_out(material: ServiceOrderMaterial, db: Session) -> ServiceOrderMaterialOut:
    product_name = None
    if material.service_product_id:
        product = db.get(ServiceProduct, material.service_product_id)
        product_name = product.name if product else None
    return ServiceOrderMaterialOut(
        id=material.id,
        service_order_id=material.service_order_id,
        service_product_id=material.service_product_id,
        service_product_name=product_name,
        description=material.description,
        quantity=material.quantity,
        unit=material.unit,
        unit_price=material.unit_price,
        notes=material.notes,
    )


def _ensure_service_product(db: Session, service_product_id: int | None) -> None:
    if not service_product_id:
        return
    product = db.get(ServiceProduct, service_product_id)
    if not product or product.is_deleted:
        raise HTTPException(status_code=404, detail='Produto/serviço do catálogo não encontrado')


@router.get('/{item_id}/materials', response_model=list[ServiceOrderMaterialOut])
def list_materials(item_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*VIEW_ROLES))):
    _fetch_order_or_404(item_id, db)
    rows = db.scalars(
        select(ServiceOrderMaterial)
        .where(ServiceOrderMaterial.service_order_id == item_id, ServiceOrderMaterial.is_deleted.is_(False))
        .order_by(ServiceOrderMaterial.id.asc())
    ).all()
    return [_material_to_out(m, db) for m in rows]


@router.post('/{item_id}/materials', response_model=ServiceOrderMaterialOut)
def create_material(
    item_id: int,
    payload: ServiceOrderMaterialIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*EDIT_ROLES)),
):
    _fetch_order_or_404(item_id, db)
    _ensure_service_product(db, payload.service_product_id)
    material = ServiceOrderMaterial(service_order_id=item_id, **payload.model_dump())
    db.add(material)
    db.commit()
    db.refresh(material)
    return _material_to_out(material, db)


@router.put('/{item_id}/materials/{material_id}', response_model=ServiceOrderMaterialOut)
def update_material(
    item_id: int,
    material_id: int,
    payload: ServiceOrderMaterialIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*EDIT_ROLES)),
):
    _fetch_order_or_404(item_id, db)
    material = _fetch_material_or_404(db, item_id, material_id)
    _ensure_service_product(db, payload.service_product_id)
    for key, value in payload.model_dump().items():
        setattr(material, key, value)
    db.commit()
    db.refresh(material)
    return _material_to_out(material, db)


@router.delete('/{item_id}/materials/{material_id}')
def delete_material(
    item_id: int,
    material_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*EDIT_ROLES)),
):
    _fetch_order_or_404(item_id, db)
    material = _fetch_material_or_404(db, item_id, material_id)
    material.is_deleted = True
    db.commit()
    return {'message': 'Material removido com sucesso'}
