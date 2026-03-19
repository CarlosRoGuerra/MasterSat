from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import require_roles
from app.db.session import get_db
from app.models.plan import Plan
from app.models.enums import UserRole
from app.schemas.plan import PlanCreate, PlanOut, PlanUpdate

router = APIRouter()
@router.get('/', response_model=list[PlanOut])
def list_items(db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    return db.scalars(select(Plan).where(Plan.is_deleted == False)).all()
@router.post('/', response_model=PlanOut)
def create_item(payload: PlanCreate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    data = payload.model_dump()

    obj = Plan(**data)
    db.add(obj); db.commit(); db.refresh(obj); return obj
@router.get('/{item_id}', response_model=PlanOut)
def get_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    obj = db.get(Plan, item_id)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail='Registro não encontrado')
    return obj
@router.put('/{item_id}', response_model=PlanOut)
def update_item(item_id: int, payload: PlanUpdate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    obj = db.get(Plan, item_id)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail='Registro não encontrado')
    data = payload.model_dump(exclude_unset=True)

    for key, value in data.items(): setattr(obj, key, value)
    db.commit(); db.refresh(obj); return obj
@router.delete('/{item_id}')
def delete_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    obj = db.get(Plan, item_id)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail='Registro não encontrado')
    obj.is_deleted = True; db.commit(); return {'message': 'Registro removido com soft delete'}
