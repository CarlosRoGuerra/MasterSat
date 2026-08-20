from __future__ import annotations

from calendar import monthrange as _monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.config import settings
from app.core.security import create_file_access_token
from app.db.session import get_db
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.document import Document
from app.models.enums import BillingStatus, DocumentReviewStatus, TrackerStatus, UserRole, VehicleStatus
from app.models.plan import Plan
from app.models.service_product import ServiceProduct
from app.models.tracker import Tracker
from app.models.uninstall_event import UninstallEvent
from app.models.vehicle import Vehicle
from app.schemas.document import DocumentDeleteOut, DocumentOut, DocumentReviewUpdate
from app.schemas.vehicle import VehicleCreate, VehicleOut, VehicleUpdate
from app.services.financial import add_months, current_cycle_bounds, decimal_to_float, period_label_for_date, prorated_amount
from app.services.storage import remove_object, upload_bytes

router = APIRouter()

VIEW_ROLES = (UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL)
EDIT_ROLES = (UserRole.ADMIN, UserRole.OPERATIONAL)


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


def _normalize_vehicle_data(data: dict) -> dict:
    if 'model_year' in data and data.get('model_year') is not None:
        data['year'] = data['model_year']
    elif 'manufacture_year' in data and data.get('manufacture_year') is not None and data.get('year') is None:
        data['year'] = data['manufacture_year']
    return data


@router.get('/', response_model=list[VehicleOut])
def list_items(
    search: str | None = None,
    status: str | None = None,
    client_id: int | None = None,
    type: str | None = None,
    skip: int = 0,
    limit: int = Query(default=50, le=500),
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
                Vehicle.contract_number.ilike(term),
                Vehicle.sales_point.ilike(term),
                Vehicle.seller_consultant.ilike(term),
                Vehicle.city.ilike(term),
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
    data = _normalize_vehicle_data(payload.model_dump())
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
    data = _normalize_vehicle_data(payload.model_dump(exclude_unset=True))

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




@router.post('/{item_id}/uninstall')
def uninstall_vehicle(
    item_id: int,
    uninstall_date: date,
    uninstall_service_product_id: int | None = None,
    uninstall_fee: float | None = None,
    destination_contract_id: int | None = None,
    destination_vehicle_id: int | None = None,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*EDIT_ROLES)),
):
    vehicle = _get_vehicle_or_404(item_id, db)
    # Idempotência: veículo já retirado não pode ser desinstalado de novo — senão
    # cada re-clique cria outra taxa de desinstalação e cancela outro contrato.
    if vehicle.status == VehicleStatus.REMOVED:
        raise HTTPException(status_code=400, detail='Este veículo já está desinstalado.')

    tracker = db.scalar(select(Tracker).where(Tracker.vehicle_id == vehicle.id, Tracker.is_deleted.is_(False)))
    # Contrato DESTE veículo (não "qualquer contrato ativo do cliente"): em clientes
    # com frota, buscar só pelo cliente cancelava o contrato do veículo errado.
    contract_filters = [
        Contract.vehicle_id == vehicle.id,
        Contract.status == 'ativo',
        Contract.is_deleted.is_(False),
    ]
    if tracker is not None:
        # Havendo rastreador, casa também pelo equipamento para desempatar
        # frotas com mais de um contrato no mesmo veículo.
        contract = db.scalar(
            select(Contract).where(*contract_filters, Contract.tracker_id == tracker.id)
            .order_by(Contract.id.desc())
        )
    else:
        contract = None
    if contract is None:
        contract = db.scalar(select(Contract).where(*contract_filters).order_by(Contract.id.desc()))

    source_prorated = None
    destination_prorated = None
    uninstall_fee_billing_id = None

    if contract:
        plan = db.get(Plan, contract.plan_id)
        if plan and plan.active:
            cycle_start, cycle_end = current_cycle_bounds(contract, plan, uninstall_date)
            source_amount = prorated_amount(plan.price, cycle_start, cycle_end, uninstall_date)
            period_label = period_label_for_date(cycle_start, getattr(plan, 'billing_interval_months', 1) or 1)
            # Se já existe cobrança gerada pelo fechamento para o período, ajusta o valor proporcionalmente.
            # Caso ainda não exista (fechamento não rodou), não cria nada — o fechamento calculará o pró-rata.
            current_billing = db.scalar(select(Billing).where(
                Billing.contract_id == contract.id,
                Billing.item_id.is_(None),
                Billing.billing_type.in_(['recorrente', 'prorata', 'primeira_mensalidade']),
                Billing.period_label == period_label,
                Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
                Billing.is_deleted.is_(False),
            ))
            if current_billing:
                current_billing.amount = source_amount
                current_billing.title = f'Plano pró-rata até desinstalação • {vehicle.plate}'
                current_billing.notes = f'Cobrança proporcional até {uninstall_date.strftime("%d/%m/%Y")}'
                source_prorated = decimal_to_float(source_amount)

    # Registra evento de desinstalação pendente — a taxa será injetada pelo motor
    # de fechamento mensal ao rodar o mês correspondente à data de retirada.
    has_fee = (uninstall_fee and uninstall_fee > 0) or uninstall_service_product_id
    if has_fee:
        fee_value = float(uninstall_fee) if (uninstall_fee and uninstall_fee > 0) else None
        notes_parts = [f'Desinstalação registrada em {uninstall_date.strftime("%d/%m/%Y")}']
        if fee_value:
            notes_parts.append(f'Taxa direta: R$ {fee_value:.2f}')
        if uninstall_service_product_id:
            notes_parts.append(f'Produto de serviço ID {uninstall_service_product_id}')
        event = UninstallEvent(
            vehicle_id=vehicle.id,
            tracker_id=tracker.id if tracker else None,
            contract_id=contract.id if contract else None,
            client_id=vehicle.client_id,
            uninstall_date=uninstall_date,
            fee_amount=fee_value,
            service_product_id=uninstall_service_product_id,
            status='pending',
            notes=' | '.join(notes_parts),
        )
        db.add(event)
        db.flush()
        uninstall_fee_billing_id = None  # será preenchido no fechamento

    if tracker:
        tracker.vehicle_id = None
        tracker.client_id = None
        tracker.status = TrackerStatus.STOCK
        tracker.install_date = None
        tracker.uninstall_date = uninstall_date
    vehicle.status = VehicleStatus.REMOVED
    vehicle.uninstalled_at = uninstall_date
    if contract:
        contract.status = 'cancelado'
        contract.end_date = uninstall_date
    db.commit()

    return {
        'message': 'Desinstalação registrada com sucesso.',
        'source_prorated_amount': source_prorated,
        'destination_prorated_amount': destination_prorated,
        'uninstall_fee_billing_id': uninstall_fee_billing_id,
        'tracker_returned_to_stock': bool(tracker),
    }

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


@router.post('/{item_id}/documents', response_model=list[DocumentOut])
async def upload_vehicle_document(
    item_id: int,
    category: str = Form(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*EDIT_ROLES)),
):
    _get_vehicle_or_404(item_id, db)
    created_documents: list[DocumentOut] = []

    normalized_category = category.strip().lower() or 'geral'
    for file in files:
        content = await file.read()
        if not content:
            continue
        object_key = f'vehicles/{item_id}/{uuid4()}-{file.filename}'
        upload_bytes(object_name=object_key, content=content, content_type=file.content_type or 'application/octet-stream')

        document = Document(
            file_name=file.filename,
            object_key=object_key,
            content_type=file.content_type or 'application/octet-stream',
            size_bytes=len(content),
            reference_type='vehicle',
            reference_id=item_id,
            category=normalized_category,
            review_status=DocumentReviewStatus.SUBMITTED,
            review_notes=None,
            active=True,
        )
        db.add(document)
        db.flush()
        created_documents.append(_document_to_out(document))

    if not created_documents:
        raise HTTPException(status_code=400, detail='Nenhum arquivo válido foi enviado')

    db.commit()
    return created_documents


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
