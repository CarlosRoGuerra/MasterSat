from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.client import Client
from app.models.enums import UserRole
from app.schemas.client import ClientCreate, ClientOut, ClientUpdate

router = APIRouter()


@router.get('/', response_model=list[ClientOut])
def list_items(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL)),
):
    return db.scalars(select(Client).where(Client.is_deleted.is_(False)).order_by(Client.id.desc())).all()


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

    obj = Client(**data)
    db.add(obj)
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

    for key, value in data.items():
        setattr(obj, key, value)
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
    db.commit()
    return {'message': 'Registro removido com soft delete'}
