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
def list_items(db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    return db.scalars(select(Client).where(Client.is_deleted == False)).all()
@router.post('/', response_model=ClientOut)
def create_item(payload: ClientCreate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    data = payload.model_dump()

    obj = Client(**data)
    db.add(obj); db.commit(); db.refresh(obj); return obj
@router.get('/{item_id}', response_model=ClientOut)
def get_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    obj = db.get(Client, item_id)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail='Registro não encontrado')
    return obj
@router.put('/{item_id}', response_model=ClientOut)
def update_item(item_id: int, payload: ClientUpdate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    obj = db.get(Client, item_id)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail='Registro não encontrado')
    data = payload.model_dump(exclude_unset=True)

    for key, value in data.items(): setattr(obj, key, value)
    db.commit(); db.refresh(obj); return obj
@router.delete('/{item_id}')
def delete_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    obj = db.get(Client, item_id)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail='Registro não encontrado')
    obj.is_deleted = True; db.commit(); return {'message': 'Registro removido com soft delete'}
