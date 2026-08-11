from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.contract import Contract
from app.models.enums import UserRole
from app.models.plan import Plan
from app.schemas.plan import PlanCreate, PlanOut, PlanUpdate

router = APIRouter()


def serialize_plan(db: Session, plan: Plan) -> PlanOut:
    active_contracts = (
        db.query(func.count(Contract.id))
        .filter(Contract.is_deleted == False, Contract.plan_id == plan.id, Contract.status == 'ativo')
        .scalar()
        or 0
    )
    def _num(v):
        return float(v) if v is not None else None

    return PlanOut(
        id=plan.id,
        name=plan.name,
        price=float(plan.price),
        description=plan.description,
        active=plan.active,
        billing_interval_months=getattr(plan, 'billing_interval_months', 1) or 1,
        default_installation_fee=_num(getattr(plan, 'default_installation_fee', None)),
        default_uninstall_fee=_num(getattr(plan, 'default_uninstall_fee', None)),
        default_billing_day=getattr(plan, 'default_billing_day', None),
        default_duration_months=getattr(plan, 'default_duration_months', None),
        active_contracts=active_contracts,
    )


@router.get('/', response_model=list[PlanOut])
def list_items(search: str | None = None, active: bool | None = None, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    query = db.query(Plan).filter(Plan.is_deleted == False)
    if search:
        query = query.filter(Plan.name.ilike(f'%{search}%'))
    if active is not None:
        query = query.filter(Plan.active == active)
    items = query.order_by(Plan.name.asc()).all()
    return [serialize_plan(db, item) for item in items]


@router.post('/', response_model=PlanOut)
def create_item(payload: PlanCreate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    existing = db.query(Plan).filter(Plan.is_deleted == False, func.lower(Plan.name) == payload.name.lower()).first()
    if existing:
        raise HTTPException(status_code=400, detail='Já existe um plano com este nome.')
    obj = Plan(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return serialize_plan(db, obj)


@router.get('/{item_id}', response_model=PlanOut)
def get_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    obj = db.get(Plan, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Plano não encontrado')
    return serialize_plan(db, obj)


@router.put('/{item_id}', response_model=PlanOut)
def update_item(item_id: int, payload: PlanUpdate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    obj = db.get(Plan, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Plano não encontrado')
    data = payload.model_dump(exclude_unset=True)
    if 'name' in data:
        existing = db.query(Plan).filter(Plan.is_deleted == False, func.lower(Plan.name) == data['name'].lower(), Plan.id != item_id).first()
        if existing:
            raise HTTPException(status_code=400, detail='Já existe outro plano com este nome.')
    for key, value in data.items():
        setattr(obj, key, value)
    db.commit()
    db.refresh(obj)
    return serialize_plan(db, obj)


@router.delete('/{item_id}')
def delete_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    obj = db.get(Plan, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Plano não encontrado')
    # Diz QUANTOS contratos travam a exclusão e o que fazer — só "possui
    # contratos ativos" deixava o operador sem saída.
    ativos = db.query(Contract).filter(
        Contract.is_deleted == False,  # noqa: E712
        Contract.plan_id == item_id,
        Contract.status == 'ativo',
    ).count()
    if ativos:
        raise HTTPException(
            status_code=400,
            detail=(
                f'O plano "{obj.name}" não pode ser removido: há {ativos} '
                f'contrato(s) ativo(s) usando ele. Migre esses contratos para '
                f'outro plano ou desative o plano em vez de excluir.'
            ),
        )
    obj.is_deleted = True
    db.commit()
    return {'message': 'Plano removido com sucesso'}
