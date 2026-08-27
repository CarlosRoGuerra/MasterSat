from __future__ import annotations

from io import BytesIO
from secrets import token_urlsafe
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import create_file_access_token, get_password_hash
from app.core.config import settings
from app.core.uploads import read_limited, safe_object_name, validate_content_type
from app.db.session import get_db
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.document import Document
from app.models.enums import DocumentReviewStatus, UserRole
from app.models.plan import Plan
from app.models.service_order import ServiceOrder
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.client import ClientCreate, ClientOut, ClientUpdate
from app.schemas.document import DocumentDeleteOut, DocumentOut, DocumentReviewUpdate
from app.services.multiportal_sync_state import (
    CLIENT_MULTIPORTAL_FIELDS,
    has_relevant_changes,
    invalidate_client_trackers,
)
from app.services.storage import remove_object, upload_bytes

router = APIRouter()

VIEW_ROLES = (UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL)
EDIT_ROLES = (UserRole.ADMIN, UserRole.OPERATIONAL)


def build_address(data: dict) -> str | None:
    parts = [
        data.get('address_line'),
        f"nº {data.get('address_number')}" if data.get('address_number') else None,
        data.get('address_complement'),
        data.get('neighborhood'),
        f"{data.get('city')}/{data.get('state')}" if data.get('city') and data.get('state') else data.get('city'),
        f"CEP {data.get('zip_code')}" if data.get('zip_code') else None,
    ]
    values = [item for item in parts if item]
    return ', '.join(values) if values else None


def _build_document_urls(document_id: int) -> tuple[str, str]:
    base = f"{settings.backend_public_url.rstrip('/')}/{settings.api_v1_prefix.lstrip('/')}"
    token = create_file_access_token(document_id)
    return (
        f"{base}/documents/{document_id}/view?token={token}",
        f"{base}/documents/{document_id}/view?token={token}&download=1",
    )


def _document_to_out(document: Document, uploaded_by: str | None = None) -> DocumentOut:
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
        created_at=getattr(document, 'created_at', None),
        uploaded_by=uploaded_by,
    )


def _uploader_names(db: Session, documents: list[Document]) -> dict[int, str]:
    """Resolve os nomes de quem enviou os documentos, em uma consulta só."""
    ids = {d.uploaded_by_user_id for d in documents if d.uploaded_by_user_id}
    if not ids:
        return {}
    return {u.id: u.name for u in db.query(User).filter(User.id.in_(ids)).all()}


def _get_client_or_404(item_id: int, db: Session) -> Client:
    obj = db.get(Client, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Registro não encontrado')
    return obj


def _sync_client_user(client: Client, db: Session) -> None:
    return None


CATEGORIA_CONTRATO = 'contrato'


def _clientes_com_contrato(db: Session, client_ids: list[int]) -> set[int]:
    """IDs dos clientes que têm um documento de contrato ativo guardado.

    Uma consulta só para a lista inteira — evita um SELECT por linha."""
    if not client_ids:
        return set()
    rows = db.execute(
        select(Document.reference_id).where(
            Document.reference_type == 'client',
            Document.reference_id.in_(client_ids),
            Document.category == CATEGORIA_CONTRATO,
            Document.active.is_(True),
        )
    ).all()
    return {r[0] for r in rows}


def _marcar_contrato(client: Client, ids_com_contrato: set[int]) -> Client:
    """Anexa a flag transitória lida pelo ClientOut (não é coluna do banco)."""
    client.contrato_armazenado = client.id in ids_com_contrato
    return client


def _marcar_um(db: Session, client: Client) -> Client:
    """Mesma flag, para retornos de um cliente só (get/create/update)."""
    return _marcar_contrato(client, _clientes_com_contrato(db, [client.id]))


@router.get('/', response_model=list[ClientOut])
def list_items(
    search: str | None = None,
    status: str | None = None,
    cpf_cnpj: str | None = None,
    type: str | None = Query(default=None),
    skip: int = 0,
    limit: int = Query(default=100, le=300),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    stmt = select(Client).where(Client.is_deleted.is_(False))

    if cpf_cnpj:
        stmt = stmt.where(Client.cpf_cnpj == cpf_cnpj.strip())
    elif search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Client.name.ilike(term),
                Client.trade_name.ilike(term),
                Client.cpf_cnpj.ilike(term),
                Client.email.ilike(term),
                Client.phone.ilike(term),
                Client.city.ilike(term),
            )
        )
    if status:
        stmt = stmt.where(Client.status == status)
    if type:
        stmt = stmt.where(Client.type == type)

    stmt = stmt.order_by(Client.id.desc()).offset(skip).limit(limit)
    clientes = list(db.scalars(stmt).all())
    com_contrato = _clientes_com_contrato(db, [c.id for c in clientes])
    return [_marcar_contrato(c, com_contrato) for c in clientes]


@router.post('/', response_model=ClientOut)
def create_item(
    payload: ClientCreate,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*EDIT_ROLES)),
):
    existing = db.scalar(select(Client).where(Client.cpf_cnpj == payload.cpf_cnpj, Client.is_deleted.is_(False)))
    if existing:
        raise HTTPException(status_code=409, detail='Já existe cliente com este CPF/CNPJ')

    data = payload.model_dump()
    if data.get('type') != 'pj':
        data['extra_emails'] = None
    elif data.get('email') and data.get('extra_emails'):
        data['extra_emails'] = [email for email in data['extra_emails'] if email != data['email']] or None

    data['address'] = build_address(data)
    obj = Client(**data)
    db.add(obj)
    db.flush()
    _sync_client_user(obj, db)
    db.commit()
    db.refresh(obj)
    return _marcar_um(db, obj)


@router.get('/{item_id}', response_model=ClientOut)
def get_item(
    item_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    return _marcar_um(db, _get_client_or_404(item_id, db))


@router.put('/{item_id}', response_model=ClientOut)
def update_item(
    item_id: int,
    payload: ClientUpdate,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*EDIT_ROLES)),
):
    obj = _get_client_or_404(item_id, db)
    data = payload.model_dump(exclude_unset=True)

    if 'cpf_cnpj' in data and data['cpf_cnpj']:
        existing = db.scalar(
            select(Client).where(
                Client.cpf_cnpj == data['cpf_cnpj'],
                Client.id != item_id,
                Client.is_deleted.is_(False),
            )
        )
        if existing:
            raise HTTPException(status_code=409, detail='Já existe cliente com este CPF/CNPJ')

    if data.get('type') == 'pf':
        data['extra_emails'] = None
    elif data.get('email') and data.get('extra_emails'):
        data['extra_emails'] = [email for email in data['extra_emails'] if email != data['email']] or None

    if any(key in data for key in ['address_line', 'address_number', 'address_complement', 'neighborhood', 'city', 'state', 'zip_code']):
        base = {
            'address_line': data.get('address_line', obj.address_line),
            'address_number': data.get('address_number', obj.address_number),
            'address_complement': data.get('address_complement', obj.address_complement),
            'neighborhood': data.get('neighborhood', obj.neighborhood),
            'city': data.get('city', obj.city),
            'state': data.get('state', obj.state),
            'zip_code': data.get('zip_code', obj.zip_code),
        }
        data['address'] = build_address(base)

    multiportal_changed = has_relevant_changes(obj, data, CLIENT_MULTIPORTAL_FIELDS)
    for key, value in data.items():
        setattr(obj, key, value)
    if multiportal_changed:
        invalidate_client_trackers(db, obj.id)
    _sync_client_user(obj, db)
    db.commit()
    db.refresh(obj)
    return _marcar_um(db, obj)


@router.delete('/{item_id}')
def delete_item(
    item_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*EDIT_ROLES)),
):
    obj = _get_client_or_404(item_id, db)
    obj.is_deleted = True
    db.commit()
    return {'message': 'Registro removido com soft delete'}


@router.get('/{item_id}/documents', response_model=list[DocumentOut])
def list_client_documents(
    item_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    _get_client_or_404(item_id, db)
    documents = db.scalars(
        select(Document)
        .where(
            Document.reference_type == 'client',
            Document.reference_id == item_id,
            Document.active.is_(True),
        )
        .order_by(Document.id.desc())
    ).all()
    nomes = _uploader_names(db, documents)
    return [_document_to_out(d, nomes.get(d.uploaded_by_user_id)) for d in documents]


@router.post('/{item_id}/documents', response_model=list[DocumentOut])
async def upload_client_document(
    item_id: int,
    category: str = Form(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EDIT_ROLES)),
):
    _get_client_or_404(item_id, db)
    created_documents: list[DocumentOut] = []

    normalized_category = category.strip().lower() or 'geral'
    for file in files:
        content_type = validate_content_type(file)
        content = await read_limited(file)
        if not content:
            continue
        object_key = f'clients/{item_id}/documents/{uuid4()}-{safe_object_name(file.filename)}'
        upload_bytes(object_name=object_key, content=content, content_type=content_type)

        document = Document(
            file_name=file.filename,
            object_key=object_key,
            content_type=content_type,
            size_bytes=len(content),
            reference_type='client',
            reference_id=item_id,
            category=normalized_category,
            review_status=DocumentReviewStatus.SUBMITTED,
            review_notes=None,
            active=True,
            uploaded_by_user_id=current_user.id,
        )
        db.add(document)
        db.flush()
        created_documents.append(_document_to_out(document, current_user.name))

    if not created_documents:
        raise HTTPException(status_code=400, detail='Nenhum arquivo válido foi enviado')

    db.commit()
    return created_documents


@router.put('/{item_id}/documents/{document_id}/review', response_model=DocumentOut)
def review_client_document(
    item_id: int,
    document_id: int,
    payload: DocumentReviewUpdate,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*EDIT_ROLES)),
):
    _get_client_or_404(item_id, db)
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.reference_type == 'client',
            Document.reference_id == item_id,
            Document.active.is_(True),
        )
    )
    if not document:
        raise HTTPException(status_code=404, detail='Documento não encontrado')
    document.review_status = payload.review_status
    document.review_notes = payload.review_notes
    _uploader = db.get(User, document.uploaded_by_user_id) if document.uploaded_by_user_id else None
    db.commit()
    db.refresh(document)
    return _document_to_out(document, _uploader.name if _uploader else None)


@router.delete('/{item_id}/documents/{document_id}', response_model=DocumentDeleteOut)
def delete_client_document(
    item_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*EDIT_ROLES)),
):
    _get_client_or_404(item_id, db)
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.reference_type == 'client',
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


@router.get('/{item_id}/timeline-pdf')
def client_timeline_pdf(
    item_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    client = _get_client_or_404(item_id, db)

    contracts = db.scalars(
        select(Contract)
        .where(Contract.client_id == item_id, Contract.is_deleted.is_(False))
        .order_by(Contract.created_at.desc())
    ).all()

    orders = db.scalars(
        select(ServiceOrder)
        .where(ServiceOrder.client_id == item_id, ServiceOrder.is_deleted.is_(False))
        .order_by(ServiceOrder.created_at.desc())
        .limit(50)
    ).all()

    billings = db.scalars(
        select(Billing)
        .where(Billing.client_id == item_id, Billing.is_deleted.is_(False))
        .order_by(Billing.due_date.desc())
        .limit(50)
    ).all()

    plan_cache: dict[int, str] = {}
    vehicle_cache: dict[int, str] = {}

    def plan_name(plan_id: int | None) -> str:
        if not plan_id:
            return '-'
        if plan_id not in plan_cache:
            p = db.get(Plan, plan_id)
            plan_cache[plan_id] = p.name if p else str(plan_id)
        return plan_cache[plan_id]

    def vehicle_plate(vehicle_id: int | None) -> str:
        if not vehicle_id:
            return '-'
        if vehicle_id not in vehicle_cache:
            v = db.get(Vehicle, vehicle_id)
            vehicle_cache[vehicle_id] = v.plate if v else str(vehicle_id)
        return vehicle_cache[vehicle_id]

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    page_w, page_h = A4
    margin = 50
    y = page_h - 60

    def check_page(needed: int = 20) -> None:
        nonlocal y
        if y < margin + needed:
            pdf.showPage()
            y = page_h - 60

    pdf.setFont('Helvetica-Bold', 16)
    pdf.drawString(margin, y, f'Linha do Tempo — {client.name}')
    y -= 20
    pdf.setFont('Helvetica', 10)
    pdf.drawString(margin, y, f'CPF/CNPJ: {client.cpf_cnpj}  |  Gerado em: {__import__("datetime").date.today().strftime("%d/%m/%Y")}')
    y -= 30
    pdf.setLineWidth(0.5)
    pdf.line(margin, y, page_w - margin, y)
    y -= 20

    if contracts:
        pdf.setFont('Helvetica-Bold', 12)
        pdf.drawString(margin, y, 'CONTRATOS')
        y -= 16
        for c in contracts:
            check_page(28)
            pdf.setFont('Helvetica', 10)
            pdf.drawString(margin + 10, y, f'Contrato #{c.id}  |  Plano: {plan_name(c.plan_id)}  |  Início: {c.start_date.strftime("%d/%m/%Y") if c.start_date else "-"}  |  Status: {c.status}')
            y -= 14
            if getattr(c, 'vehicle_id', None):
                pdf.drawString(margin + 20, y, f'Veículo: {vehicle_plate(c.vehicle_id)}')
                y -= 14

    y -= 10
    check_page(30)
    if orders:
        pdf.setFont('Helvetica-Bold', 12)
        pdf.drawString(margin, y, 'ORDENS DE SERVIÇO')
        y -= 16
        for o in orders:
            check_page(28)
            pdf.setFont('Helvetica', 10)
            dt = (o.executed_at or o.scheduled_at or o.created_at)
            dt_str = dt.strftime('%d/%m/%Y') if dt else '-'
            pdf.drawString(margin + 10, y, f'OS #{o.number}  |  {o.type.value if hasattr(o.type, "value") else o.type}  |  Status: {o.status.value if hasattr(o.status, "value") else o.status}  |  Data: {dt_str}')
            y -= 14
            if o.vehicle_id:
                pdf.drawString(margin + 20, y, f'Veículo: {vehicle_plate(o.vehicle_id)}')
                y -= 14

    y -= 10
    check_page(30)
    if billings:
        pdf.setFont('Helvetica-Bold', 12)
        pdf.drawString(margin, y, 'COBRANÇAS')
        y -= 16
        for b in billings:
            check_page(28)
            pdf.setFont('Helvetica', 10)
            title = b.title or getattr(b, 'plan_name', None) or 'Cobrança'
            amount = float(b.amount) if b.amount else 0
            due = b.due_date.strftime('%d/%m/%Y') if b.due_date else '-'
            paid = b.payment_date.strftime('%d/%m/%Y') if b.payment_date else '-'
            status = b.status.value if hasattr(b.status, 'value') else str(b.status)
            pdf.drawString(margin + 10, y, f'{title}  |  R$ {amount:.2f}  |  Venc: {due}  |  Status: {status}  |  Pago: {paid}')
            y -= 14

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    filename = f'timeline-cliente-{item_id}.pdf'
    return StreamingResponse(buffer, media_type='application/pdf', headers={'Content-Disposition': f'inline; filename={filename}'})
