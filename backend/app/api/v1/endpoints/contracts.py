from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus, UserRole
from app.models.plan import Plan
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.schemas.contract import ContractCreate, ContractOut, ContractUpdate
from app.services.financial import refresh_overdue_statuses

router = APIRouter()


def serialize_contract(db: Session, contract: Contract) -> ContractOut:
    client = db.get(Client, contract.client_id)
    plan = db.get(Plan, contract.plan_id)
    vehicle = db.get(Vehicle, contract.vehicle_id) if getattr(contract, 'vehicle_id', None) else None
    tracker = db.get(Tracker, contract.tracker_id) if getattr(contract, 'tracker_id', None) else None
    open_billings = (
        db.query(func.count(Billing.id))
        .filter(
            Billing.is_deleted == False,
            Billing.contract_id == contract.id,
            Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
        )
        .scalar()
        or 0
    )
    next_due = (
        db.query(Billing.due_date)
        .filter(
            Billing.is_deleted == False,
            Billing.contract_id == contract.id,
            Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
        )
        .order_by(Billing.due_date.asc())
        .first()
    )
    return ContractOut(
        id=contract.id,
        client_id=contract.client_id,
        interveniente_client_id=getattr(contract, 'interveniente_client_id', None),
        plan_id=contract.plan_id,
        vehicle_id=getattr(contract, 'vehicle_id', None),
        tracker_id=getattr(contract, 'tracker_id', None),
        start_date=contract.start_date,
        end_date=contract.end_date,
        status=contract.status,
        billing_day=contract.billing_day,
        payment_method=contract.payment_method,
        bank=getattr(contract, 'bank', None),
        notes=contract.notes,
        installation_fee=float(contract.installation_fee) if contract.installation_fee is not None else None,
        uninstall_fee=float(contract.uninstall_fee) if contract.uninstall_fee is not None else None,
        client_name=client.name if (client and not client.is_deleted) else None,
        plan_name=plan.name if (plan and not plan.is_deleted) else None,
        vehicle_plate=vehicle.plate if (vehicle and not vehicle.is_deleted) else None,
        tracker_identifier=(tracker.imei if (tracker and not tracker.is_deleted) else None),
        monthly_value=float(plan.price) if (plan and not plan.is_deleted) else None,
        open_billings=open_billings,
        next_due_date=next_due[0] if next_due else None,
    )


def validate_links(db: Session, client_id: int, vehicle_id: int | None, tracker_id: int | None):
    if vehicle_id:
        vehicle = db.get(Vehicle, vehicle_id)
        if not vehicle or vehicle.is_deleted:
            raise HTTPException(status_code=404, detail='Veículo não encontrado')
        if vehicle.client_id != client_id:
            raise HTTPException(status_code=400, detail='O veículo selecionado não pertence ao cliente informado.')
    if tracker_id:
        tracker = db.get(Tracker, tracker_id)
        if not tracker or tracker.is_deleted:
            raise HTTPException(status_code=404, detail='Rastreador não encontrado')
        if tracker.client_id and tracker.client_id != client_id:
            raise HTTPException(status_code=400, detail='O rastreador selecionado não pertence ao cliente informado.')
        if vehicle_id and tracker.vehicle_id and tracker.vehicle_id != vehicle_id:
            raise HTTPException(status_code=400, detail='O rastreador selecionado está vinculado a outro veículo.')


@router.get('/', response_model=list[ContractOut])
def list_items(
    client_id: int | None = None,
    interveniente_client_id: int | None = None,
    plan_id: int | None = None,
    vehicle_id: int | None = None,
    tracker_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = Query(default=100, le=300),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL)),
):
    refresh_overdue_statuses(db)
    query = db.query(Contract).filter(Contract.is_deleted == False)
    if client_id:
        query = query.filter(Contract.client_id == client_id)
    if interveniente_client_id:
        query = query.filter(Contract.interveniente_client_id == interveniente_client_id)
    if plan_id:
        query = query.filter(Contract.plan_id == plan_id)
    if vehicle_id:
        query = query.filter(Contract.vehicle_id == vehicle_id)
    if tracker_id:
        query = query.filter(Contract.tracker_id == tracker_id)
    if status:
        query = query.filter(Contract.status == status)
    if search:
        term = f'%{search.strip()}%'
        client_ids = db.scalars(
            select(Client.id).where(Client.name.ilike(term), Client.is_deleted.is_(False))
        ).all()
        vehicle_ids = db.scalars(
            select(Vehicle.id).where(Vehicle.plate.ilike(term), Vehicle.is_deleted.is_(False))
        ).all()
        from sqlalchemy import or_
        query = query.filter(
            or_(
                Contract.client_id.in_(client_ids),
                Contract.vehicle_id.in_(vehicle_ids),
            )
        )
    items = query.order_by(Contract.created_at.desc()).limit(limit).all()
    return [serialize_contract(db, item) for item in items]


@router.post('/', response_model=ContractOut)
def create_item(payload: ContractCreate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    client = db.get(Client, payload.client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail='Cliente não encontrado')
    plan = db.get(Plan, payload.plan_id)
    if not plan or plan.is_deleted:
        raise HTTPException(status_code=404, detail='Plano não encontrado')
    validate_links(db, payload.client_id, payload.vehicle_id, payload.tracker_id)
    data = payload.model_dump()
    if not data.get('billing_day'):
        data['billing_day'] = payload.start_date.day if payload.start_date.day <= 28 else 28
    obj = Contract(**data)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return serialize_contract(db, obj)



@router.get('/{item_id}', response_model=ContractOut)
def get_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    obj = db.get(Contract, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Contrato não encontrado')
    return serialize_contract(db, obj)


@router.put('/{item_id}', response_model=ContractOut)
def update_item(item_id: int, payload: ContractUpdate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    obj = db.get(Contract, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Contrato não encontrado')
    data = payload.model_dump(exclude_unset=True)
    client_id = data.get('client_id', obj.client_id)
    if 'client_id' in data:
        client = db.get(Client, client_id)
        if not client or client.is_deleted:
            raise HTTPException(status_code=404, detail='Cliente não encontrado')
    if 'plan_id' in data:
        plan = db.get(Plan, data['plan_id'])
        if not plan or plan.is_deleted:
            raise HTTPException(status_code=404, detail='Plano não encontrado')
    validate_links(db, client_id, data.get('vehicle_id', getattr(obj, 'vehicle_id', None)), data.get('tracker_id', getattr(obj, 'tracker_id', None)))
    for key, value in data.items():
        setattr(obj, key, value)
    db.commit()
    db.refresh(obj)
    return serialize_contract(db, obj)


@router.delete('/{item_id}')
def delete_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    obj = db.get(Contract, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Contrato não encontrado')
    obj.is_deleted = True
    obj.status = 'cancelado'
    db.commit()
    return {'message': 'Contrato removido com sucesso'}


@router.get('/{item_id}/pdf')
def contract_pdf(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    """Gera o PDF do contrato (TERMO DE ADESÃO + cláusulas) no padrão MasterSat."""
    obj = db.get(Contract, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Contrato não encontrado')
    client = db.get(Client, obj.client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail='Cliente do contrato não encontrado')
    plan = db.get(Plan, obj.plan_id)
    vehicle = db.get(Vehicle, obj.vehicle_id) if getattr(obj, 'vehicle_id', None) else None
    from app.services.contract_pdf import gerar_contrato_pdf
    pdf = gerar_contrato_pdf(obj, client, plan, vehicle)
    return Response(
        content=pdf,
        media_type='application/pdf',
        headers={'Content-Disposition': f'inline; filename="contrato_{obj.id}.pdf"'},
    )
