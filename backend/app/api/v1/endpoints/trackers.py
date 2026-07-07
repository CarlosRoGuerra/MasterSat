from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.db.session import get_db
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus, TrackerStatus, UserRole
from app.models.plan import Plan
from app.models.tracker import Tracker
from app.models.tracker_history import TrackerHistory
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.contract import ContractOut
from app.schemas.tracker import TrackerCreate, TrackerHistoryOut, TrackerLinkPayload, TrackerOut, TrackerUpdate

router = APIRouter()

VIEW_ROLES = (UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL)
EDIT_ROLES = (UserRole.ADMIN, UserRole.OPERATIONAL)


def _tracker_to_out(tracker: Tracker, db: Session) -> TrackerOut:
    client_name = None
    vehicle_plate = None
    vehicle_model = None
    active_plan_id = None
    active_plan_name = None
    if tracker.client_id:
        client = db.get(Client, tracker.client_id)
        if client and not client.is_deleted:
            client_name = client.name
    if tracker.vehicle_id:
        vehicle = db.get(Vehicle, tracker.vehicle_id)
        if vehicle and not vehicle.is_deleted:
            vehicle_plate = vehicle.plate
            vehicle_model = vehicle.model
    active_contract = db.scalar(
        select(Contract)
        .where(
            Contract.tracker_id == tracker.id,
            Contract.is_deleted.is_(False),
            Contract.status == 'ativo',
        )
        .order_by(Contract.id.desc())
    )
    if active_contract:
        plan = db.get(Plan, active_contract.plan_id)
        if plan and not plan.is_deleted:
            active_plan_id = plan.id
            active_plan_name = plan.name
    return TrackerOut(
        id=tracker.id,
        imei=tracker.imei,
        brand=tracker.brand,
        model=tracker.model,
        status=tracker.status,
        sim_number=tracker.sim_number,
        sim_iccid=tracker.sim_iccid,
        carrier=tracker.carrier,
        sim_status=tracker.sim_status,
        firmware=tracker.firmware,
        external_manufacturer_id=tracker.external_manufacturer_id,
        external_manufacturer_label=tracker.external_manufacturer_label,
        ip_address=tracker.ip_address,
        port=tracker.port,
        install_location=tracker.install_location,
        chip_type=tracker.chip_type,
        equipment_type=tracker.equipment_type,
        communication_type=tracker.communication_type,
        service_plan_name=tracker.service_plan_name,
        installation_fee=float(tracker.installation_fee) if tracker.installation_fee is not None else None,
        acquisition_date=tracker.acquisition_date,
        install_date=tracker.install_date,
        warranty_until=tracker.warranty_until,
        notes=tracker.notes,
        client_id=tracker.client_id,
        vehicle_id=tracker.vehicle_id,
        client_name=client_name,
        vehicle_plate=vehicle_plate,
        vehicle_model=vehicle_model,
        active_plan_id=active_plan_id,
        active_plan_name=active_plan_name,
        integration_status=tracker.integration_status,
        integration_last_code=tracker.integration_last_code,
        integration_last_description=tracker.integration_last_description,
        integration_last_transaction_id=tracker.integration_last_transaction_id,
        created_at=tracker.created_at,
        updated_at=tracker.updated_at,
    )



def _get_tracker_or_404(item_id: int, db: Session) -> Tracker:
    tracker = db.scalar(select(Tracker).where(Tracker.id == item_id, Tracker.is_deleted.is_(False)))
    if not tracker:
        raise HTTPException(status_code=404, detail='Rastreador não encontrado')
    return tracker



def _get_vehicle_or_404(vehicle_id: int, db: Session) -> Vehicle:
    vehicle = db.scalar(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.is_deleted.is_(False)))
    if not vehicle:
        raise HTTPException(status_code=404, detail='Veículo não encontrado')
    return vehicle



def _get_client_or_404(client_id: int, db: Session) -> Client:
    client = db.scalar(select(Client).where(Client.id == client_id, Client.is_deleted.is_(False)))
    if not client:
        raise HTTPException(status_code=404, detail='Cliente não encontrado')
    return client



def _ensure_imei_available(imei: str, db: Session, ignore_id: int | None = None) -> None:
    stmt = select(Tracker).where(Tracker.imei == imei, Tracker.is_deleted.is_(False))
    if ignore_id is not None:
        stmt = stmt.where(Tracker.id != ignore_id)
    existing = db.scalar(stmt)
    if existing:
        raise HTTPException(status_code=409, detail='Já existe rastreador com este IMEI/ID')



def _ensure_vehicle_assignment_available(vehicle_id: int | None, db: Session, ignore_tracker_id: int | None = None) -> None:
    # Múltiplos rastreadores por veículo são permitidos (cada um com seu plano/contrato).
    # Mantemos a função para compatibilidade mas sem bloquear.
    pass



def _normalize_payload(data: dict, db: Session) -> dict:
    vehicle_id = data.get('vehicle_id')
    client_id = data.get('client_id')

    imei = data.get('imei')
    if imei:
        data['serial_number'] = imei

    if vehicle_id:
        vehicle = _get_vehicle_or_404(vehicle_id, db)
        data['client_id'] = vehicle.client_id
        if not data.get('install_date') and data.get('status') == TrackerStatus.INSTALLED:
            data['install_date'] = date.today()
    elif client_id:
        _get_client_or_404(client_id, db)

    if data.get('external_manufacturer_label') is not None:
        data['external_manufacturer_label'] = data['external_manufacturer_label'].strip() or None
    return data



def _register_history(
    db: Session,
    tracker: Tracker,
    *,
    action: str,
    previous_vehicle_id: int | None,
    new_vehicle_id: int | None,
    previous_client_id: int | None,
    new_client_id: int | None,
    previous_status: str | None,
    new_status: str | None,
    created_by_user_id: int | None,
    notes: str | None = None,
) -> None:
    history = TrackerHistory(
        tracker_id=tracker.id,
        action=action,
        previous_vehicle_id=previous_vehicle_id,
        new_vehicle_id=new_vehicle_id,
        previous_client_id=previous_client_id,
        new_client_id=new_client_id,
        previous_status=previous_status,
        new_status=new_status,
        event_date=date.today(),
        notes=notes,
        created_by_user_id=created_by_user_id,
    )
    db.add(history)


@router.get('/', response_model=list[TrackerOut])
def list_items(
    search: str | None = None,
    status: str | None = None,
    client_id: int | None = None,
    vehicle_id: int | None = None,
    skip: int = 0,
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    stmt = select(Tracker).where(Tracker.is_deleted.is_(False))
    if search:
        term = f'%{search.strip()}%'
        stmt = stmt.where(
            or_(
                Tracker.imei.ilike(term),
                Tracker.serial_number.ilike(term),
                Tracker.brand.ilike(term),
                Tracker.model.ilike(term),
                Tracker.sim_number.ilike(term),
                Tracker.sim_iccid.ilike(term),
            )
        )
    if status:
        stmt = stmt.where(Tracker.status == status)
    if client_id:
        stmt = stmt.where(Tracker.client_id == client_id)
    if vehicle_id:
        stmt = stmt.where(Tracker.vehicle_id == vehicle_id)
    trackers = db.scalars(stmt.order_by(Tracker.id.desc()).offset(skip).limit(limit)).all()
    return [_tracker_to_out(item, db) for item in trackers]


@router.post('/', response_model=TrackerOut)
def create_item(
    payload: TrackerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EDIT_ROLES)),
):
    data = _normalize_payload(payload.model_dump(), db)
    _ensure_imei_available(data['imei'], db)
    _ensure_vehicle_assignment_available(data.get('vehicle_id'), db)

    tracker = Tracker(**data)
    db.add(tracker)
    db.flush()
    _register_history(
        db,
        tracker,
        action='created',
        previous_vehicle_id=None,
        new_vehicle_id=tracker.vehicle_id,
        previous_client_id=None,
        new_client_id=tracker.client_id,
        previous_status=None,
        new_status=tracker.status.value if isinstance(tracker.status, TrackerStatus) else str(tracker.status),
        created_by_user_id=current_user.id,
        notes='Cadastro inicial do rastreador',
    )
    db.commit()
    db.refresh(tracker)
    return _tracker_to_out(tracker, db)


@router.get('/{item_id}', response_model=TrackerOut)
def get_item(
    item_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    tracker = _get_tracker_or_404(item_id, db)
    return _tracker_to_out(tracker, db)


@router.get('/{item_id}/history', response_model=list[TrackerHistoryOut])
def get_history(
    item_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    _get_tracker_or_404(item_id, db)
    history = db.scalars(
        select(TrackerHistory)
        .where(TrackerHistory.tracker_id == item_id)
        .order_by(TrackerHistory.created_at.desc(), TrackerHistory.id.desc())
    ).all()
    return history


@router.put('/{item_id}', response_model=TrackerOut)
def update_item(
    item_id: int,
    payload: TrackerUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EDIT_ROLES)),
):
    tracker = _get_tracker_or_404(item_id, db)
    previous_vehicle_id = tracker.vehicle_id
    previous_client_id = tracker.client_id
    previous_status = tracker.status.value if isinstance(tracker.status, TrackerStatus) else str(tracker.status)

    data = _normalize_payload(payload.model_dump(exclude_unset=True), db)
    if 'imei' in data and data['imei'] is not None:
        _ensure_imei_available(data['imei'], db, ignore_id=item_id)
    if 'vehicle_id' in data:
        _ensure_vehicle_assignment_available(data.get('vehicle_id'), db, ignore_tracker_id=item_id)

    for key, value in data.items():
        setattr(tracker, key, value)

    new_vehicle_id = tracker.vehicle_id
    new_client_id = tracker.client_id
    new_status = tracker.status.value if isinstance(tracker.status, TrackerStatus) else str(tracker.status)

    action = 'updated'
    notes = 'Dados gerais atualizados'
    if previous_vehicle_id != new_vehicle_id:
        action = 'linked' if new_vehicle_id else 'unlinked'
        notes = 'Vínculo do rastreador atualizado'
    elif previous_status != new_status:
        action = 'status_changed'
        notes = 'Status do rastreador atualizado'

    _register_history(
        db,
        tracker,
        action=action,
        previous_vehicle_id=previous_vehicle_id,
        new_vehicle_id=new_vehicle_id,
        previous_client_id=previous_client_id,
        new_client_id=new_client_id,
        previous_status=previous_status,
        new_status=new_status,
        created_by_user_id=current_user.id,
        notes=notes,
    )
    db.commit()
    db.refresh(tracker)
    return _tracker_to_out(tracker, db)


@router.delete('/{item_id}')
def delete_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EDIT_ROLES)),
):
    tracker = _get_tracker_or_404(item_id, db)
    tracker.is_deleted = True
    _register_history(
        db,
        tracker,
        action='deleted',
        previous_vehicle_id=tracker.vehicle_id,
        new_vehicle_id=None,
        previous_client_id=tracker.client_id,
        new_client_id=None,
        previous_status=tracker.status.value if isinstance(tracker.status, TrackerStatus) else str(tracker.status),
        new_status='excluido',
        created_by_user_id=current_user.id,
        notes='Rastreador removido com soft delete',
    )
    tracker.vehicle_id = None
    tracker.client_id = None
    db.commit()
    return {'message': 'Rastreador removido com soft delete'}


def _serialize_contract(db: Session, contract: Contract) -> ContractOut:
    from app.models.client import Client as ClientModel
    client = db.get(ClientModel, contract.client_id)
    plan = db.get(Plan, contract.plan_id)
    vehicle = db.get(Vehicle, contract.vehicle_id) if contract.vehicle_id else None
    tracker = db.get(Tracker, contract.tracker_id) if contract.tracker_id else None
    open_billings = (
        db.query(Billing.id)
        .filter(
            Billing.is_deleted.is_(False),
            Billing.contract_id == contract.id,
            Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
        )
        .count()
    )
    next_due = db.scalar(
        select(Billing.due_date)
        .where(
            Billing.is_deleted.is_(False),
            Billing.contract_id == contract.id,
            Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
        )
        .order_by(Billing.due_date.asc())
    )
    return ContractOut(
        id=contract.id,
        client_id=contract.client_id,
        plan_id=contract.plan_id,
        vehicle_id=contract.vehicle_id,
        tracker_id=contract.tracker_id,
        start_date=contract.start_date,
        end_date=contract.end_date,
        status=contract.status,
        billing_day=contract.billing_day,
        payment_method=contract.payment_method,
        notes=contract.notes,
        client_name=client.name if client else None,
        plan_name=plan.name if plan else None,
        vehicle_plate=vehicle.plate if vehicle else None,
        tracker_identifier=tracker.imei if tracker else None,
        monthly_value=float(plan.price) if plan else None,
        open_billings=open_billings,
        next_due_date=next_due,
    )


@router.post('/{item_id}/link-vehicle', response_model=dict)
def link_vehicle(
    item_id: int,
    payload: TrackerLinkPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EDIT_ROLES)),
):
    """Vincula rastreador a um veículo e, opcionalmente, cria o contrato com plano."""
    tracker = _get_tracker_or_404(item_id, db)
    vehicle = _get_vehicle_or_404(payload.vehicle_id, db)

    if not vehicle.client_id:
        raise HTTPException(status_code=400, detail='O veículo não possui cliente vinculado.')

    previous_vehicle_id = tracker.vehicle_id
    previous_client_id = tracker.client_id
    previous_status = tracker.status.value if isinstance(tracker.status, TrackerStatus) else str(tracker.status)

    tracker.vehicle_id = vehicle.id
    tracker.client_id = vehicle.client_id
    tracker.serial_number = tracker.serial_number or tracker.imei
    if tracker.status == TrackerStatus.STOCK:
        tracker.status = TrackerStatus.INSTALLED
        tracker.install_date = tracker.install_date or date.today()

    _register_history(
        db, tracker,
        action='linked',
        previous_vehicle_id=previous_vehicle_id,
        new_vehicle_id=vehicle.id,
        previous_client_id=previous_client_id,
        new_client_id=vehicle.client_id,
        previous_status=previous_status,
        new_status=tracker.status.value if isinstance(tracker.status, TrackerStatus) else str(tracker.status),
        created_by_user_id=current_user.id,
        notes=f'Vinculado ao veículo {vehicle.plate}' + (f' com plano #{payload.plan_id}' if payload.plan_id else ''),
    )

    contract_out: ContractOut | None = None
    if payload.plan_id:
        plan = db.get(Plan, payload.plan_id)
        if not plan or plan.is_deleted:
            raise HTTPException(status_code=404, detail='Plano não encontrado.')

        # Dia de vencimento: usa payload > cliente > dia do mês de início (cap 28)
        client_obj = db.get(Client, vehicle.client_id)
        client_billing_day = getattr(client_obj, 'billing_day', None) if client_obj else None
        billing_day = payload.billing_day or client_billing_day or (
            payload.start_date.day if payload.start_date.day <= 28 else 28
        )

        interveniente_id = payload.interveniente_client_id
        if interveniente_id == vehicle.client_id:
            interveniente_id = None  # o próprio cliente → não registra interveniente
        if interveniente_id:
            interveniente = db.get(Client, interveniente_id)
            if not interveniente or interveniente.is_deleted:
                raise HTTPException(status_code=404, detail='Interveniente não encontrado na base de clientes.')

        contract = Contract(
            client_id=vehicle.client_id,
            interveniente_client_id=interveniente_id,
            plan_id=payload.plan_id,
            vehicle_id=vehicle.id,
            tracker_id=tracker.id,
            start_date=payload.start_date,
            status='ativo',
            billing_day=billing_day,
            payment_method=payload.payment_method,
            billing_modality=payload.billing_modality,
            bank=payload.bank or 'ailos',
            notes=payload.notes,
        )
        db.add(contract)
        db.flush()
        contract_out = _serialize_contract(db, contract)

    db.commit()
    db.refresh(tracker)

    return {
        'tracker': _tracker_to_out(tracker, db),
        'contract': contract_out,
        'message': f'Rastreador vinculado ao veículo {vehicle.plate}' + (' com contrato criado.' if contract_out else '.'),
    }
