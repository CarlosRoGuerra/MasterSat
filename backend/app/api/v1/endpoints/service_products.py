from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.integrity import raise_integrity_conflict
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.service_product import ServiceProduct
from app.schemas.service_product import ServiceProductCreate, ServiceProductOut, ServiceProductUpdate

router = APIRouter()

# Mesma corrida check-then-insert de plans.py: o pré-check por nome não é
# atômico com o INSERT, então duas criações concorrentes com o mesmo nome
# ainda dependem do UNIQUE de schema (`ix_service_products_name`) para barrar
# a duplicata — isto traduz o IntegrityError numa mensagem de domínio.
_SERVICE_PRODUCT_INTEGRITY_MESSAGES = {
    'ix_service_products_name': 'Já existe um serviço/produto com este nome.',
}
_SERVICE_PRODUCT_SQLITE_CONSTRAINTS = {
    'UNIQUE constraint failed: service_products.name': 'ix_service_products_name',
}


@router.get('/', response_model=list[ServiceProductOut])
def list_items(search: str | None = None, active: bool | None = None, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    query = db.query(ServiceProduct).filter(ServiceProduct.is_deleted == False)
    if search:
        query = query.filter(ServiceProduct.name.ilike(f'%{search}%'))
    if active is not None:
        query = query.filter(ServiceProduct.active == active)
    return query.order_by(ServiceProduct.name.asc()).all()


@router.post('/', response_model=ServiceProductOut)
def create_item(payload: ServiceProductCreate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    existing = db.query(ServiceProduct).filter(ServiceProduct.is_deleted == False, func.lower(ServiceProduct.name) == payload.name.lower()).first()
    if existing:
        raise HTTPException(status_code=400, detail='Já existe um serviço/produto com este nome.')
    obj = ServiceProduct(**payload.model_dump())
    db.add(obj)
    try:
        db.commit()
    except IntegrityError as exc:
        raise_integrity_conflict(
            db, exc, _SERVICE_PRODUCT_INTEGRITY_MESSAGES,
            sqlite_columns=_SERVICE_PRODUCT_SQLITE_CONSTRAINTS,
        )
    db.refresh(obj)
    return obj


@router.put('/{item_id}', response_model=ServiceProductOut)
def update_item(item_id: int, payload: ServiceProductUpdate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    obj = db.get(ServiceProduct, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Serviço/produto não encontrado')
    data = payload.model_dump(exclude_unset=True)
    if 'name' in data:
        existing = db.query(ServiceProduct).filter(ServiceProduct.is_deleted == False, func.lower(ServiceProduct.name) == data['name'].lower(), ServiceProduct.id != item_id).first()
        if existing:
            raise HTTPException(status_code=400, detail='Já existe outro serviço/produto com este nome.')
    for key, value in data.items():
        setattr(obj, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        raise_integrity_conflict(
            db, exc, _SERVICE_PRODUCT_INTEGRITY_MESSAGES,
            sqlite_columns=_SERVICE_PRODUCT_SQLITE_CONSTRAINTS,
        )
    db.refresh(obj)
    return obj


@router.delete('/{item_id}')
def delete_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    obj = db.get(ServiceProduct, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Serviço/produto não encontrado')
    obj.is_deleted = True
    db.commit()
    return {'message': 'Serviço/produto removido com sucesso'}
