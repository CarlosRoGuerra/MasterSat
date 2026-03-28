from secrets import token_urlsafe

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import get_password_hash
from app.db.session import get_db
from app.models.client import Client
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.client import ClientCreate, ClientOut, ClientUpdate

router = APIRouter()


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


def _sync_client_user(client: Client, db: Session) -> None:
    linked_user = db.scalar(select(User).where(User.client_id == client.id, User.role == UserRole.CLIENT))
    normalized_email = client.email.strip().lower() if client.email else None

    if not normalized_email:
        if linked_user and not linked_user.is_deleted:
            linked_user.is_deleted = True
            linked_user.active = False
        return

    email_owner = db.scalar(
        select(User).where(
            User.email == normalized_email,
            User.id != (linked_user.id if linked_user else 0),
            User.is_deleted.is_(False),
        )
    )
    if email_owner:
        raise HTTPException(status_code=409, detail='Já existe uma conta de acesso usando este e-mail')

    if not linked_user:
        linked_user = User(
            name=client.name,
            email=normalized_email,
            password_hash=get_password_hash(token_urlsafe(16)),
            role=UserRole.CLIENT,
            active=True,
            client_id=client.id,
        )
        db.add(linked_user)
    else:
        linked_user.name = client.name
        linked_user.email = normalized_email
        linked_user.active = True
        linked_user.is_deleted = False

@router.get('/', response_model=list[ClientOut])
def list_items(
    search: str | None = None,
    status: str | None = None,
    type: str | None = Query(default=None),
    skip: int = 0,
    limit: int = Query(default=100, le=300),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL)),
):
    stmt = select(Client).where(Client.is_deleted.is_(False))

    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Client.name.ilike(term),
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
    return db.scalars(stmt).all()


@router.post('/', response_model=ClientOut)
def create_item(
    payload: ClientCreate,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL)),
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
    return obj


@router.get('/{item_id}', response_model=ClientOut)
def get_item(
    item_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL)),
):
    obj = db.get(Client, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Registro não encontrado')
    return obj


@router.put('/{item_id}', response_model=ClientOut)
def update_item(
    item_id: int,
    payload: ClientUpdate,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL)),
):
    obj = db.get(Client, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Registro não encontrado')

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

    for key, value in data.items():
        setattr(obj, key, value)
    _sync_client_user(obj, db)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete('/{item_id}')
def delete_item(
    item_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL)),
):
    obj = db.get(Client, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Registro não encontrado')
    obj.is_deleted = True
    linked_user = db.scalar(select(User).where(User.client_id == obj.id, User.role == UserRole.CLIENT, User.is_deleted.is_(False)))
    if linked_user:
        linked_user.is_deleted = True
        linked_user.active = False
    db.commit()
    return {'message': 'Registro removido com soft delete'}
