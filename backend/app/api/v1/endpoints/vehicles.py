from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.client import Client
from app.models.document import Document
from app.models.enums import DocumentReviewStatus, UserRole
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.schemas.document import DocumentDeleteOut, DocumentOut, DocumentReviewUpdate
from app.schemas.vehicle import VehicleCreate, VehicleOut, VehicleUpdate
from app.core.config import settings
from app.core.security import create_file_access_token
from app.services.storage import remove_object, upload_bytes

router = APIRouter()


VIEW_ROLES = (UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL)
EDIT_ROLES = (UserRole.ADMIN, UserRole.OPERATIONAL)


def _vehicle_to_out(vehicle: Vehicle) -> VehicleOut:
    return VehicleOut.model_validate(vehicle)



def _document_to_out(document: Document) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        file_name=document.file_name,
        category=document.category,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        review_status=document.review_status,
        review_notes=document.review_notes,
        url=f"{settings.backend_public_url.rstrip('/')}/{settings.api_v1_prefix.lstrip('/')}/documents/{document.id}/view?token={create_file_access_token(document.id)}",
    )



def _get_vehicle_or_404(item_id: int, db: Session) -> Vehicle:
    obj = db.scalar(select(Vehicle).where(Vehicle.id == item_id, Vehicle.is_deleted.is_(False)))
    if not obj:
        raise HTTPException(status_code=404, detail='Veículo não encontrado')
    return obj



def _ensure_client_exists(client_id: int, db: Session) -> None:
    client = db.scalar(select(Client).where(Client.id == client_id, Client.is_deleted.is_(False)))
    if not client:
        raise HTTPException(status_code=404, detail='Cliente não encontrado')



def _ensure_plate_available(plate: str, db: Session, ignore_id: int | None = None) -> None:
    stmt = select(Vehicle).where(Vehicle.plate == plate, Vehicle.is_deleted.is_(False))
    if ignore_id is not None:
        stmt = stmt.where(Vehicle.id != ignore_id)
    exists = db.scalar(stmt)
    if exists:
        raise HTTPException(status_code=400, detail='Já existe veículo com essa placa')



def _ensure_chassis_available(chassis: str | None, db: Session, ignore_id: int | None = None) -> None:
    if not chassis:
        return
    stmt = select(Vehicle).where(Vehicle.chassis == chassis, Vehicle.is_deleted.is_(False))
    if ignore_id is not None:
        stmt = stmt.where(Vehicle.id != ignore_id)
    exists = db.scalar(stmt)
    if exists:
        raise HTTPException(status_code=400, detail='Já existe veículo com esse chassi')


@router.get('/', response_model=list[VehicleOut])
def list_items(
    search: str | None = None,
    status: str | None = None,
    client_id: int | None = None,
    type: str | None = None,
    skip: int = 0,
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    stmt = select(Vehicle).where(Vehicle.is_deleted.is_(False))
    if search:
        term = f'%{search.strip()}%'
        stmt = stmt.where(
            or_(
                Vehicle.plate.ilike(term),
                Vehicle.chassis.ilike(term),
                Vehicle.renavam.ilike(term),
                Vehicle.brand.ilike(term),
                Vehicle.model.ilike(term),
            )
        )
    if status:
        stmt = stmt.where(Vehicle.status == status)
    if client_id:
        stmt = stmt.where(Vehicle.client_id == client_id)
    if type:
        stmt = stmt.where(func.lower(Vehicle.type) == type.strip().lower())
    stmt = stmt.order_by(Vehicle.id.desc()).offset(skip).limit(limit)
    return db.scalars(stmt).all()


@router.post('/', response_model=VehicleOut)
def create_item(
    payload: VehicleCreate,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*EDIT_ROLES)),
):
    data = payload.model_dump()
    _ensure_client_exists(data['client_id'], db)
    _ensure_plate_available(data['plate'], db)
    _ensure_chassis_available(data.get('chassis'), db)
    obj = Vehicle(**data)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get('/{item_id}', response_model=VehicleOut)
def get_item(
    item_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    return _get_vehicle_or_404(item_id, db)


@router.put('/{item_id}', response_model=VehicleOut)
def update_item(
    item_id: int,
    payload: VehicleUpdate,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*EDIT_ROLES)),
):
    obj = _get_vehicle_or_404(item_id, db)
    data = payload.model_dump(exclude_unset=True)

    if 'client_id' in data and data['client_id'] is not None:
        _ensure_client_exists(data['client_id'], db)
    if 'plate' in data and data['plate'] is not None:
        _ensure_plate_available(data['plate'], db, ignore_id=item_id)
    if 'chassis' in data:
        _ensure_chassis_available(data['chassis'], db, ignore_id=item_id)

    for key, value in data.items():
        setattr(obj, key, value)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete('/{item_id}')
def delete_item(
    item_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN)),
):
    obj = _get_vehicle_or_404(item_id, db)
    linked_tracker = db.scalar(
        select(Tracker).where(Tracker.vehicle_id == obj.id, Tracker.is_deleted.is_(False))
    )
    if linked_tracker:
        raise HTTPException(status_code=400, detail='Não é possível excluir: existe rastreador vinculado ao veículo')
    obj.is_deleted = True
    db.commit()
    return {'message': 'Veículo removido com soft delete'}


@router.get('/{item_id}/documents', response_model=list[DocumentOut])
def list_vehicle_documents(
    item_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    _get_vehicle_or_404(item_id, db)
    documents = db.scalars(
        select(Document)
        .where(
            Document.reference_type == 'vehicle',
            Document.reference_id == item_id,
            Document.active.is_(True),
        )
        .order_by(Document.id.desc())
    ).all()
    return [_document_to_out(document) for document in documents]


@router.post('/{item_id}/documents', response_model=DocumentOut)
async def upload_vehicle_document(
    item_id: int,
    category: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*EDIT_ROLES)),
):
    _get_vehicle_or_404(item_id, db)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail='Arquivo vazio')
    object_key = f'vehicles/{item_id}/{uuid4()}-{file.filename}'
    upload_bytes(object_name=object_key, content=content, content_type=file.content_type or 'application/octet-stream')

    document = Document(
        file_name=file.filename,
        object_key=object_key,
        content_type=file.content_type or 'application/octet-stream',
        size_bytes=len(content),
        reference_type='vehicle',
        reference_id=item_id,
        category=category.strip().lower() or 'geral',
        review_status=DocumentReviewStatus.SUBMITTED,
        review_notes=None,
        active=True,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    return _document_to_out(document)


@router.put('/{item_id}/documents/{document_id}/review', response_model=DocumentOut)
def review_vehicle_document(
    item_id: int,
    document_id: int,
    payload: DocumentReviewUpdate,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*EDIT_ROLES)),
):
    _get_vehicle_or_404(item_id, db)
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.reference_type == 'vehicle',
            Document.reference_id == item_id,
            Document.active.is_(True),
        )
    )
    if not document:
        raise HTTPException(status_code=404, detail='Documento não encontrado')
    document.review_status = payload.review_status
    document.review_notes = payload.review_notes
    db.commit()
    db.refresh(document)
    return _document_to_out(document)


@router.delete('/{item_id}/documents/{document_id}', response_model=DocumentDeleteOut)
def delete_vehicle_document(
    item_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*EDIT_ROLES)),
):
    _get_vehicle_or_404(item_id, db)
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.reference_type == 'vehicle',
            Document.reference_id == item_id,
            Document.active.is_(True),
        )
    )
    if not document:
        raise HTTPException(status_code=404, detail='Documento não encontrado')
    try:
        remove_object(document.object_key)
    except Exception:
        pass
    document.active = False
    db.commit()
    return DocumentDeleteOut(message='Documento removido com sucesso')
