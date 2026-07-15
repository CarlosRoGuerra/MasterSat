"""
Contas a PAGAR (fornecedores, aluguel, chips, impostos etc.).

GET    /payables/          → lista (filtros: status, search, vencimento)
POST   /payables/          → cadastrar conta
PUT    /payables/{id}      → editar
POST   /payables/{id}/pay  → marcar como paga
POST   /payables/{id}/cancel → cancelar
DELETE /payables/{id}      → soft delete
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.payable import Payable
from app.schemas.payable import PayableCreate, PayableOut, PayablePay, PayableUpdate

router = APIRouter()

ALLOWED_ROLES = (UserRole.ADMIN, UserRole.FINANCIAL)


def _get_or_404(item_id: int, db: Session) -> Payable:
    obj = db.get(Payable, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Conta não encontrada')
    return obj


def _serialize(obj: Payable) -> PayableOut:
    overdue = 0
    if obj.status == 'pendente' and obj.due_date < date.today():
        overdue = (date.today() - obj.due_date).days
    return PayableOut(
        id=obj.id,
        description=obj.description,
        supplier=obj.supplier,
        category=obj.category,
        amount=float(obj.amount),
        due_date=obj.due_date,
        status=obj.status,
        payment_date=obj.payment_date,
        payment_method=obj.payment_method,
        notes=obj.notes,
        overdue_days=overdue,
    )


@router.get('/', response_model=list[PayableOut])
def list_items(
    status: str | None = None,
    search: str | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    query = db.query(Payable).filter(Payable.is_deleted.is_(False))
    if status:
        query = query.filter(Payable.status == status)
    if search:
        term = f'%{search.strip()}%'
        query = query.filter(or_(
            Payable.description.ilike(term),
            Payable.supplier.ilike(term),
            Payable.category.ilike(term),
        ))
    if due_from:
        query = query.filter(Payable.due_date >= due_from)
    if due_to:
        query = query.filter(Payable.due_date <= due_to)
    items = query.order_by(Payable.due_date.asc(), Payable.id.asc()).limit(limit).all()
    return [_serialize(i) for i in items]


@router.post('/', response_model=PayableOut)
def create_item(payload: PayableCreate, db: Session = Depends(get_db), _: object = Depends(require_roles(*ALLOWED_ROLES))):
    obj = Payable(**payload.model_dump(), status='pendente')
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return _serialize(obj)


@router.put('/{item_id}', response_model=PayableOut)
def update_item(item_id: int, payload: PayableUpdate, db: Session = Depends(get_db), _: object = Depends(require_roles(*ALLOWED_ROLES))):
    obj = _get_or_404(item_id, db)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, key, value)
    db.commit()
    db.refresh(obj)
    return _serialize(obj)


@router.post('/{item_id}/pay', response_model=PayableOut)
def pay_item(item_id: int, payload: PayablePay, db: Session = Depends(get_db), _: object = Depends(require_roles(*ALLOWED_ROLES))):
    obj = _get_or_404(item_id, db)
    if obj.status == 'paga':
        raise HTTPException(status_code=400, detail='Conta já está paga')
    obj.status = 'paga'
    obj.payment_date = payload.payment_date
    obj.payment_method = payload.payment_method
    if payload.notes:
        obj.notes = f'{obj.notes} | {payload.notes}' if obj.notes else payload.notes
    db.commit()
    db.refresh(obj)
    return _serialize(obj)


@router.post('/{item_id}/cancel', response_model=PayableOut)
def cancel_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(*ALLOWED_ROLES))):
    obj = _get_or_404(item_id, db)
    if obj.status == 'paga':
        raise HTTPException(status_code=400, detail='Conta paga não pode ser cancelada')
    obj.status = 'cancelada'
    db.commit()
    db.refresh(obj)
    return _serialize(obj)


@router.delete('/{item_id}')
def delete_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(*ALLOWED_ROLES))):
    obj = _get_or_404(item_id, db)
    obj.is_deleted = True
    db.commit()
    return {'message': 'Conta removida'}
