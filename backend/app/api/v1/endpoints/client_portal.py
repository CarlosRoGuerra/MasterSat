from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import asc, func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.config import settings
from app.core.security import create_file_access_token
from app.db.session import get_db
from app.models.billing import Billing
from app.models.client import Client
from app.models.document import Document
from app.models.enums import BillingStatus, DocumentReviewStatus, UserRole, VehicleStatus
from app.models.tracker import Tracker
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.client_portal import (
    ClientBillingOut,
    ClientDashboardOut,
    ClientDashboardSummaryOut,
    ClientProfileOut,
    ClientProfileUpdate,
    ClientVehicleDocumentOut,
    ClientVehicleOut,
)
from app.services.storage import remove_object, upload_bytes

router = APIRouter()

ALLOWED_CLIENT_DOC_CATEGORIES = {
    'cnh',
    'rg',
    'cpf',
    'contrato',
    'comprovante_endereco',
    'cartao_cnpj',
    'contrato_social',
    'outro',
}


def _build_document_urls(document_id: int) -> tuple[str, str]:
    base = f"{settings.backend_public_url.rstrip('/')}/{settings.api_v1_prefix.lstrip('/')}"
    token = create_file_access_token(document_id)
    return (
        f"{base}/documents/{document_id}/view?token={token}",
        f"{base}/documents/{document_id}/view?token={token}&download=1",
    )


def _get_current_client(current_user: User, db: Session) -> Client:
    if not current_user.client_id:
        raise HTTPException(status_code=404, detail='Cliente vinculado não encontrado')

    client = db.get(Client, current_user.client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail='Cliente vinculado não encontrado')
    return client


def _vehicle_to_out(vehicle: Vehicle, tracker: Tracker | None = None) -> ClientVehicleOut:
    return ClientVehicleOut(
        id=vehicle.id,
        plate=vehicle.plate,
        model=vehicle.model,
        brand=vehicle.brand,
        year=vehicle.year,
        manufacture_year=vehicle.manufacture_year,
        model_year=vehicle.model_year,
        status=vehicle.status,
        type=vehicle.type,
        chassis=vehicle.chassis,
        renavam=vehicle.renavam,
        color=vehicle.color,
        contract_number=vehicle.contract_number,
        fuel_type=vehicle.fuel_type,
        tracker_id=tracker.id if tracker else None,
        tracker_imei=tracker.imei if tracker else None,
        tracker_status=tracker.status.value if tracker else None,
        tracker_brand=tracker.brand if tracker else None,
        tracker_model=tracker.model if tracker else None,
        tracker_sim_number=tracker.sim_number if tracker else None,
        tracker_carrier=tracker.carrier if tracker else None,
    )


def _document_to_out(document: Document) -> ClientVehicleDocumentOut:
    view_url, download_url = _build_document_urls(document.id)
    return ClientVehicleDocumentOut(
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


def _build_address(client: Client) -> str | None:
    parts = [
        client.address_line,
        f'nº {client.address_number}' if client.address_number else None,
        client.address_complement,
        client.neighborhood,
        f'{client.city}/{client.state}' if client.city and client.state else client.city,
        f'CEP {client.zip_code}' if client.zip_code else None,
    ]
    values = [part for part in parts if part]
    return ', '.join(values) if values else None


def _get_vehicle_for_client(vehicle_id: int, client_id: int, db: Session) -> Vehicle:
    vehicle = db.scalar(
        select(Vehicle).where(
            Vehicle.id == vehicle_id,
            Vehicle.client_id == client_id,
            Vehicle.is_deleted.is_(False),
        )
    )
    if not vehicle:
        raise HTTPException(status_code=404, detail='Veículo não encontrado')
    return vehicle


@router.get('/dashboard', response_model=ClientDashboardOut)
def client_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.CLIENT)),
):
    client = _get_current_client(current_user, db)

    vehicles = db.scalars(
        select(Vehicle)
        .where(Vehicle.client_id == client.id, Vehicle.is_deleted.is_(False))
        .order_by(asc(Vehicle.plate))
    ).all()

    recent_billings = db.scalars(
        select(Billing)
        .where(Billing.client_id == client.id, Billing.is_deleted.is_(False))
        .order_by(Billing.due_date.desc())
        .limit(6)
    ).all()

    client_documents = db.scalars(
        select(Document)
        .where(
            Document.reference_type == 'client',
            Document.reference_id == client.id,
            Document.active.is_(True),
        )
        .order_by(Document.id.desc())
    ).all()

    today = date.today()
    pending_billings = db.scalar(
        select(func.count(Billing.id)).where(
            Billing.client_id == client.id,
            Billing.status == BillingStatus.PENDING,
            Billing.is_deleted.is_(False),
        )
    ) or 0
    overdue_billings = db.scalar(
        select(func.count(Billing.id)).where(
            Billing.client_id == client.id,
            Billing.due_date < today,
            Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
            Billing.is_deleted.is_(False),
        )
    ) or 0
    total_open_amount = db.scalar(
        select(func.coalesce(func.sum(Billing.amount), 0)).where(
            Billing.client_id == client.id,
            Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
            Billing.is_deleted.is_(False),
        )
    ) or 0

    tracker_map = {item.vehicle_id: item for item in db.scalars(select(Tracker).where(Tracker.vehicle_id.in_([vehicle.id for vehicle in vehicles]), Tracker.is_deleted.is_(False))).all()} if vehicles else {}

    return ClientDashboardOut(
        profile=ClientProfileOut(
            id=client.id,
            name=client.name,
            cpf_cnpj=client.cpf_cnpj,
            email=client.email,
            extra_emails=client.extra_emails,
            phone=client.phone,
            zip_code=client.zip_code,
            address_line=client.address_line,
            address_number=client.address_number,
            address_complement=client.address_complement,
            neighborhood=client.neighborhood,
            city=client.city,
            state=client.state,
            status=client.status,
            type=client.type,
        ),
        summary=ClientDashboardSummaryOut(
            total_vehicles=len(vehicles),
            active_vehicles=sum(1 for item in vehicles if item.status in {VehicleStatus.ACTIVE, VehicleStatus.APPROVED}),
            pending_billings=int(pending_billings),
            overdue_billings=int(overdue_billings),
            total_open_amount=float(total_open_amount),
        ),
        vehicles=[_vehicle_to_out(item, tracker_map.get(item.id)) for item in vehicles],
        recent_billings=[
            ClientBillingOut(
                id=item.id,
                amount=float(item.amount),
                due_date=item.due_date,
                status=item.status,
                payment_date=item.payment_date,
                payment_method=item.payment_method,
            )
            for item in recent_billings
        ],
        client_documents=[_document_to_out(item) for item in client_documents],
    )


@router.put('/profile', response_model=ClientProfileOut)
def update_profile(
    payload: ClientProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.CLIENT)),
):
    client = _get_current_client(current_user, db)
    data = payload.model_dump(exclude_unset=True)

    if data.get('email'):
        existing_user = db.scalar(
            select(User).where(
                User.email == data['email'],
                User.id != current_user.id,
                User.is_deleted.is_(False),
            )
        )
        if existing_user:
            raise HTTPException(status_code=409, detail='Já existe outra conta usando este e-mail')
        current_user.email = data['email']
    if client.type != 'pj':
        data['extra_emails'] = None
    elif data.get('email') and data.get('extra_emails'):
        data['extra_emails'] = [email for email in data['extra_emails'] if email != data['email']] or None

    for key, value in data.items():
        setattr(client, key, value)

    client.address = _build_address(client)
    db.commit()
    db.refresh(client)
    db.refresh(current_user)

    return ClientProfileOut(
        id=client.id,
        name=client.name,
        cpf_cnpj=client.cpf_cnpj,
        email=client.email,
        extra_emails=client.extra_emails,
        phone=client.phone,
        zip_code=client.zip_code,
        address_line=client.address_line,
        address_number=client.address_number,
        address_complement=client.address_complement,
        neighborhood=client.neighborhood,
        city=client.city,
        state=client.state,
        status=client.status,
        type=client.type,
    )


@router.get('/vehicles', response_model=list[ClientVehicleOut])
def list_my_vehicles(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.CLIENT)),
):
    client = _get_current_client(current_user, db)
    vehicles = db.scalars(
        select(Vehicle)
        .where(Vehicle.client_id == client.id, Vehicle.is_deleted.is_(False))
        .order_by(Vehicle.id.desc())
    ).all()
    tracker_map = {item.vehicle_id: item for item in db.scalars(select(Tracker).where(Tracker.vehicle_id.in_([vehicle.id for vehicle in vehicles]), Tracker.is_deleted.is_(False))).all()} if vehicles else {}
    return [_vehicle_to_out(item, tracker_map.get(item.id)) for item in vehicles]


@router.get('/vehicles/{vehicle_id}/documents', response_model=list[ClientVehicleDocumentOut])
def list_my_vehicle_documents(
    vehicle_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.CLIENT)),
):
    client = _get_current_client(current_user, db)
    _get_vehicle_for_client(vehicle_id, client.id, db)
    documents = db.scalars(
        select(Document)
        .where(
            Document.reference_type == 'vehicle',
            Document.reference_id == vehicle_id,
            Document.active.is_(True),
        )
        .order_by(Document.id.desc())
    ).all()
    return [_document_to_out(item) for item in documents]


@router.get('/documents', response_model=list[ClientVehicleDocumentOut])
def list_my_documents(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.CLIENT)),
):
    client = _get_current_client(current_user, db)
    documents = db.scalars(
        select(Document)
        .where(
            Document.reference_type == 'client',
            Document.reference_id == client.id,
            Document.active.is_(True),
        )
        .order_by(Document.id.desc())
    ).all()
    return [_document_to_out(item) for item in documents]


@router.post('/documents', response_model=ClientVehicleDocumentOut)
async def upload_my_document(
    category: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.CLIENT)),
):
    client = _get_current_client(current_user, db)
    category = category.strip().lower()
    if category not in ALLOWED_CLIENT_DOC_CATEGORIES:
        raise HTTPException(status_code=400, detail='Categoria de documento do cliente inválida')

    # Leitura limitada (o perfil CLIENTE não é confiável): não deixa um upload
    # gigante estourar a memória mesmo sem Content-Length correto.
    content = await file.read(settings.max_upload_bytes + 1)
    if not content:
        raise HTTPException(status_code=400, detail='Arquivo vazio')
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail='Arquivo excede o tamanho máximo permitido.')

    # Nome seguro para a chave do objeto (sem separadores de caminho).
    safe_name = (file.filename or 'arquivo').replace('/', '_').replace('\\', '_').strip() or 'arquivo'
    object_key = f'clients/{client.id}/documents/{date.today().isoformat()}-{safe_name}'
    upload_bytes(object_name=object_key, content=content, content_type=file.content_type or 'application/octet-stream')

    document = Document(
        file_name=file.filename,
        object_key=object_key,
        content_type=file.content_type or 'application/octet-stream',
        size_bytes=len(content),
        reference_type='client',
        reference_id=client.id,
        category=category,
        review_status=DocumentReviewStatus.SUBMITTED,
        review_notes=None,
        active=True,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return _document_to_out(document)


@router.delete('/documents/{document_id}')
def delete_my_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.CLIENT)),
):
    client = _get_current_client(current_user, db)
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.reference_type == 'client',
            Document.reference_id == client.id,
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
    return {'message': 'Documento removido com sucesso'}
