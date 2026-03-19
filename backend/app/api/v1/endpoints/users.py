from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import require_roles
from app.db.session import get_db
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.user import UserCreate, UserOut, UserUpdate
from app.core.security import get_password_hash
router = APIRouter()
@router.get('/', response_model=list[UserOut])
def list_items(db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    return db.scalars(select(User).where(User.is_deleted == False)).all()
@router.post('/', response_model=UserOut)
def create_item(payload: UserCreate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    data = payload.model_dump()
    data['password_hash'] = get_password_hash(data.pop('password'))

    obj = User(**data)
    db.add(obj); db.commit(); db.refresh(obj); return obj
@router.get('/{item_id}', response_model=UserOut)
def get_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    obj = db.get(User, item_id)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail='Registro não encontrado')
    return obj
@router.put('/{item_id}', response_model=UserOut)
def update_item(item_id: int, payload: UserUpdate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    obj = db.get(User, item_id)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail='Registro não encontrado')
    data = payload.model_dump(exclude_unset=True)
    if 'password' in data:
        data['password_hash'] = get_password_hash(data.pop('password'))

    for key, value in data.items(): setattr(obj, key, value)
    db.commit(); db.refresh(obj); return obj
@router.delete('/{item_id}')
def delete_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL))):
    obj = db.get(User, item_id)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail='Registro não encontrado')
    obj.is_deleted = True; db.commit(); return {'message': 'Registro removido com soft delete'}
