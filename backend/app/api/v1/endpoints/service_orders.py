from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import require_roles
from app.db.session import get_db
from app.models.service_order import ServiceOrder
from app.models.enums import UserRole
from app.schemas.service_order import ServiceOrderCreate, ServiceOrderOut, ServiceOrderUpdate

router = APIRouter()
@router.get('/', response_model=list[ServiceOrderOut])
def list_items(db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    return db.scalars(select(ServiceOrder).where(ServiceOrder.is_deleted == False)).all()
@router.post('/', response_model=ServiceOrderOut)
def create_item(payload: ServiceOrderCreate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    data = payload.model_dump()

    obj = ServiceOrder(**data)
    db.add(obj); db.commit(); db.refresh(obj); return obj
@router.get('/{item_id}', response_model=ServiceOrderOut)
def get_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    obj = db.get(ServiceOrder, item_id)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail='Registro não encontrado')
    return obj
@router.put('/{item_id}', response_model=ServiceOrderOut)
def update_item(item_id: int, payload: ServiceOrderUpdate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    obj = db.get(ServiceOrder, item_id)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail='Registro não encontrado')
    data = payload.model_dump(exclude_unset=True)

    for key, value in data.items(): setattr(obj, key, value)
    db.commit(); db.refresh(obj); return obj
@router.delete('/{item_id}')
def delete_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    obj = db.get(ServiceOrder, item_id)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail='Registro não encontrado')
    obj.is_deleted = True; db.commit(); return {'message': 'Registro removido com soft delete'}
