"""
Contas a PAGAR (fornecedores, aluguel, chips, impostos etc.).

GET    /payables/              → lista (filtros: status, search, vencimento)
POST   /payables/              → cadastrar conta
PUT    /payables/{id}          → editar
POST   /payables/{id}/pay      → marcar como paga
POST   /payables/{id}/cancel   → cancelar
POST   /payables/{id}/estornar → desfazer pagamento (volta a pendente)
DELETE /payables/{id}          → soft delete
GET    /payables/{id}/historico → alterações (antes/depois, responsável)

Máquina de estados (FIN-08), a mesma ideia do contas a receber:

    pendente ──pagar──▶ paga ──estornar──▶ pendente
    pendente ──cancelar──▶ cancelada       (terminal)

* valor e vencimento só mudam em ``pendente``; descrição, fornecedor,
  categoria e observações podem ser corrigidos em qualquer estado;
* conta paga não é cancelada nem removida — o histórico de despesa é
  preservado; para desfazer, estorno com justificativa;
* toda transição trava a linha (FOR UPDATE) e revalida o estado, e toda
  mudança fica em ``payable_change_logs``.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.payable import Payable
from app.models.payable_change_log import PayableChangeLog
from app.schemas.payable import (
    PayableCancel,
    PayableChangeLogOut,
    PayableCreate,
    PayableOut,
    PayablePay,
    PayableRefund,
    PayableUpdate,
)

router = APIRouter()

ALLOWED_ROLES = (UserRole.ADMIN, UserRole.FINANCIAL)

_CAMPOS_FINANCEIROS = ('amount', 'due_date')


def _get_or_404(item_id: int, db: Session) -> Payable:
    obj = db.get(Payable, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Conta não encontrada')
    return obj


def _lock_or_404(item_id: int, db: Session) -> Payable:
    """Trava a conta e relê o estado confirmado (pagar × cancelar em paralelo)."""
    obj = db.scalar(
        select(Payable)
        .where(Payable.id == item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Conta não encontrada')
    return obj


def _conflito(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={'code': code, 'message': message})


def _log(db: Session, obj: Payable, user_id: int | None, action: str, *, field: str | None = None,
         previous=None, new=None, justification: str | None = None) -> None:
    db.add(PayableChangeLog(
        payable_id=obj.id,
        changed_by_user_id=user_id,
        action=action,
        field_name=field,
        previous_value=None if previous is None else str(previous),
        new_value=None if new is None else str(new),
        justification=justification,
    ))


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
def create_item(payload: PayableCreate, db: Session = Depends(get_db), current_user=Depends(require_roles(*ALLOWED_ROLES))):
    obj = Payable(**payload.model_dump(), status='pendente')
    db.add(obj)
    db.flush()
    _log(db, obj, current_user.id, 'criacao', new=f'{obj.description} · R$ {obj.amount} · venc. {obj.due_date}')
    db.commit()
    db.refresh(obj)
    return _serialize(obj)


@router.put('/{item_id}', response_model=PayableOut)
def update_item(item_id: int, payload: PayableUpdate, db: Session = Depends(get_db), current_user=Depends(require_roles(*ALLOWED_ROLES))):
    obj = _lock_or_404(item_id, db)
    data = payload.model_dump(exclude_unset=True)
    justification = data.pop('justification', None)
    if data.get('amount') is not None:
        # Centavos exatos: comparação e histórico sem ruído de float.
        data['amount'] = Decimal(str(data['amount'])).quantize(Decimal('0.01'))
    financeiros = [
        campo for campo in _CAMPOS_FINANCEIROS
        if campo in data and data[campo] is not None and data[campo] != getattr(obj, campo)
    ]
    if financeiros and obj.status != 'pendente':
        raise _conflito(
            'conta_em_estado_terminal',
            f'Conta {obj.status} não tem valor/vencimento alterados — isso mudaria despesas já '
            'fechadas. Estorne o pagamento (conta paga) ou lance uma nova conta.',
        )
    for key, value in data.items():
        if value is None and key in ('description', *_CAMPOS_FINANCEIROS):
            continue  # campo obrigatório: None não apaga
        previous = getattr(obj, key)
        if previous != value:
            _log(db, obj, current_user.id, 'edicao', field=key, previous=previous, new=value,
                 justification=justification)
            setattr(obj, key, value)
    db.commit()
    db.refresh(obj)
    return _serialize(obj)


@router.post('/{item_id}/pay', response_model=PayableOut)
def pay_item(item_id: int, payload: PayablePay, db: Session = Depends(get_db), current_user=Depends(require_roles(*ALLOWED_ROLES))):
    obj = _lock_or_404(item_id, db)
    if obj.status == 'paga':
        raise HTTPException(status_code=400, detail='Conta já está paga')
    if obj.status != 'pendente':
        raise _conflito('conta_cancelada', 'Conta cancelada não pode ser paga. Lance uma nova conta.')
    obj.status = 'paga'
    obj.payment_date = payload.payment_date
    obj.payment_method = payload.payment_method
    if payload.notes:
        obj.notes = f'{obj.notes} | {payload.notes}' if obj.notes else payload.notes
    _log(db, obj, current_user.id, 'pagamento', field='status', previous='pendente', new='paga',
         justification=f'{payload.payment_date} · {payload.payment_method}')
    db.commit()
    db.refresh(obj)
    return _serialize(obj)


@router.post('/{item_id}/cancel', response_model=PayableOut)
def cancel_item(
    item_id: int,
    payload: PayableCancel | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*ALLOWED_ROLES)),
):
    obj = _lock_or_404(item_id, db)
    if obj.status == 'paga':
        raise HTTPException(status_code=400, detail='Conta paga não pode ser cancelada')
    if obj.status == 'cancelada':
        return _serialize(obj)
    obj.status = 'cancelada'
    _log(db, obj, current_user.id, 'cancelamento', field='status', previous='pendente', new='cancelada',
         justification=(payload.reason if payload else None))
    db.commit()
    db.refresh(obj)
    return _serialize(obj)


@router.post('/{item_id}/estornar', response_model=PayableOut)
def refund_item(item_id: int, payload: PayableRefund, db: Session = Depends(get_db), current_user=Depends(require_roles(*ALLOWED_ROLES))):
    """Desfaz um pagamento lançado: volta a pendente. Data e forma do
    pagamento desfeito ficam no histórico."""
    obj = _lock_or_404(item_id, db)
    if obj.status != 'paga':
        raise _conflito('conta_nao_paga', 'Só conta paga pode ter o pagamento estornado.')
    _log(db, obj, current_user.id, 'estorno', field='status', previous='paga', new='pendente',
         justification=f'{payload.justificativa} (pagamento desfeito: {obj.payment_date} · {obj.payment_method})')
    obj.status = 'pendente'
    obj.payment_date = None
    obj.payment_method = None
    db.commit()
    db.refresh(obj)
    return _serialize(obj)


@router.delete('/{item_id}')
def delete_item(item_id: int, db: Session = Depends(get_db), current_user=Depends(require_roles(*ALLOWED_ROLES))):
    obj = _lock_or_404(item_id, db)
    if obj.status == 'paga':
        raise _conflito(
            'conta_paga',
            'Conta paga não pode ser removida: é histórico de despesa. Estorne o pagamento antes, '
            'se o lançamento estiver errado.',
        )
    _log(db, obj, current_user.id, 'exclusao', previous=obj.status)
    obj.is_deleted = True
    db.commit()
    return {'message': 'Conta removida'}


@router.get('/{item_id}/historico', response_model=list[PayableChangeLogOut])
def item_history(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(*ALLOWED_ROLES))):
    return db.scalars(
        select(PayableChangeLog)
        .where(PayableChangeLog.payable_id == item_id)
        .order_by(PayableChangeLog.id.desc())
    ).all()
