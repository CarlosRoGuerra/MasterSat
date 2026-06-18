from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.billing import Billing
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus, UserRole
from app.models.service_product import ServiceProduct
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.schemas.client_charge_item import ClientChargeItemCreate, ClientChargeItemOut, ClientChargeItemUpdate
from app.services.financial import decimal_to_float

router = APIRouter()


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


def serialize_item(db: Session, item: ClientChargeItem) -> ClientChargeItemOut:
    client = db.get(Client, item.client_id)
    service_product = db.get(ServiceProduct, item.service_product_id) if item.service_product_id else None
    vehicle = db.get(Vehicle, item.vehicle_id) if getattr(item, 'vehicle_id', None) else None
    tracker = db.get(Tracker, item.tracker_id) if getattr(item, 'tracker_id', None) else None
    open_installments = db.query(func.count(Billing.id)).filter(Billing.is_deleted == False, Billing.item_id == item.id, Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE])).scalar() or 0
    return ClientChargeItemOut(
        id=item.id,
        client_id=item.client_id,
        contract_id=item.contract_id,
        vehicle_id=item.vehicle_id,
        tracker_id=getattr(item, 'tracker_id', None),
        service_product_id=item.service_product_id,
        title=item.title,
        description=item.description,
        quantity=item.quantity,
        unit_price=decimal_to_float(item.unit_price),
        total_amount=decimal_to_float(item.total_amount),
        installment_count=item.installment_count,
        start_date=item.start_date,
        active=item.active,
        remove_after_payment=item.remove_after_payment,
        completed_at=item.completed_at,
        status=item.status,
        client_name=client.name if client else None,
        vehicle_plate=vehicle.plate if vehicle else None,
        tracker_identifier=(tracker.imei if tracker else None),
        service_product_name=service_product.name if service_product else None,
        open_installments=open_installments,
    )


@router.get('/', response_model=list[ClientChargeItemOut])
def list_items(client_id: int | None = None, active: bool | None = None, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    query = db.query(ClientChargeItem).filter(ClientChargeItem.is_deleted == False)
    if client_id:
        query = query.filter(ClientChargeItem.client_id == client_id)
    if active is not None:
        query = query.filter(ClientChargeItem.active == active)
    return [serialize_item(db, item) for item in query.order_by(ClientChargeItem.created_at.desc()).all()]


@router.post('/', response_model=ClientChargeItemOut)
def create_item(payload: ClientChargeItemCreate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    client = db.get(Client, payload.client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail='Cliente não encontrado')
    if payload.contract_id:
        contract = db.get(Contract, payload.contract_id)
        if not contract or contract.is_deleted:
            raise HTTPException(status_code=404, detail='Contrato não encontrado')
    validate_links(db, payload.client_id, payload.vehicle_id, payload.tracker_id)
    service_product = db.get(ServiceProduct, payload.service_product_id) if payload.service_product_id else None
    if payload.service_product_id and (not service_product or service_product.is_deleted):
        raise HTTPException(status_code=404, detail='Serviço/produto não encontrado')
    remove_after_payment = payload.remove_after_payment or (service_product.remove_after_payment if service_product else False)
    total_amount = payload.quantity * payload.unit_price
    obj = ClientChargeItem(
        **payload.model_dump(exclude={'auto_generate_billings', 'remove_after_payment'}),
        total_amount=total_amount,
        remove_after_payment=remove_after_payment,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return serialize_item(db, obj)


@router.put('/{item_id}', response_model=ClientChargeItemOut)
def update_item(item_id: int, payload: ClientChargeItemUpdate, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    obj = db.get(ClientChargeItem, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Lançamento não encontrado')
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(obj, key, value)
    if 'quantity' in data or 'unit_price' in data:
        obj.total_amount = obj.quantity * obj.unit_price
    db.commit()
    db.refresh(obj)
    return serialize_item(db, obj)


@router.delete('/{item_id}')
def delete_item(item_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL))):
    obj = db.get(ClientChargeItem, item_id)
    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail='Lançamento não encontrado')
    obj.is_deleted = True
    obj.active = False
    obj.status = 'removido'
    db.commit()
    return {'message': 'Lançamento removido com sucesso'}
