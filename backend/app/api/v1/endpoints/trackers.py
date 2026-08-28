from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.integrity import integrity_conflict_detail, raise_integrity_conflict
from app.db.session import get_db
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus, TrackerStatus, UserRole, VehicleStatus
from app.models.plan import Plan
from app.models.tracker import Tracker
from app.models.tracker_history import TrackerHistory
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.contract import ContractOut
from app.schemas.tracker import (
    TrackerCreate,
    TrackerHistoryOut,
    TrackerLinkPayload,
    TrackerLoteIn,
    TrackerLoteItem,
    TrackerLoteOut,
    TrackerOut,
    TrackerUpdate,
)
from app.schemas.pagination import Page
from app.services.multiportal_lifecycle import (
    LifecycleResult,
    LifecycleSyncError,
    add_lifecycle_logs,
    apply_tracker_integration_result,
    commit_with_compensation,
    compensate_successful_transfer,
    transfer_tracker_assignment,
)
from app.services.multiportal_sync_state import (
    TRACKER_MULTIPORTAL_FIELDS,
    has_relevant_changes,
    invalidate_tracker,
)
from app.services.multiportal_outbox import (
    enqueue_full_sync,
)

router = APIRouter()

_TRACKER_INTEGRITY_MESSAGES = {
    'uq_trackers_imei_active': 'Já existe rastreador com este IMEI/ID',
    'ix_trackers_imei': 'Já existe rastreador com este IMEI/ID',
}
_TRACKER_SQLITE_CONSTRAINTS = {
    'UNIQUE constraint failed: trackers.imei': 'uq_trackers_imei_active',
}

VIEW_ROLES = (UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL)
EDIT_ROLES = (UserRole.ADMIN, UserRole.OPERATIONAL)


def _tracker_to_out(
    tracker: Tracker,
    db: Session,
    *,
    client_map: dict[int, Client] | None = None,
    vehicle_map: dict[int, Vehicle] | None = None,
    active_contract_map: dict[int, Contract] | None = None,
    plan_map: dict[int, Plan] | None = None,
) -> TrackerOut:
    # Mapas opcionais: quem lista várias trackers de uma vez (list_items) monta
    # tudo antes com queries em lote (IN) e passa aqui — evita N+1. Chamadas com
    # uma única tracker (create/update/get) seguem sem mapa e fazem o db.get()
    # avulso de sempre.
    client_name = None
    vehicle_plate = None
    vehicle_model = None
    active_plan_id = None
    active_plan_name = None
    if tracker.client_id:
        client = client_map.get(tracker.client_id) if client_map is not None else db.get(Client, tracker.client_id)
        if client and not client.is_deleted:
            client_name = client.name
    if tracker.vehicle_id:
        vehicle = vehicle_map.get(tracker.vehicle_id) if vehicle_map is not None else db.get(Vehicle, tracker.vehicle_id)
        if vehicle and not vehicle.is_deleted:
            vehicle_plate = vehicle.plate
            vehicle_model = vehicle.model
    if active_contract_map is not None:
        active_contract = active_contract_map.get(tracker.id)
    else:
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
        plan = plan_map.get(active_contract.plan_id) if plan_map is not None else db.get(Plan, active_contract.plan_id)
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


@router.get('/', response_model=Page[TrackerOut])
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
    filtro = select(Tracker).where(Tracker.is_deleted.is_(False))
    if search:
        term = f'%{search.strip()}%'
        filtro = filtro.where(
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
        filtro = filtro.where(Tracker.status == status)
    if client_id:
        filtro = filtro.where(Tracker.client_id == client_id)
    if vehicle_id:
        filtro = filtro.where(Tracker.vehicle_id == vehicle_id)
    total = db.scalar(select(func.count()).select_from(filtro.subquery())) or 0
    trackers = db.scalars(filtro.order_by(Tracker.id.desc()).offset(skip).limit(limit)).all()

    client_ids = {t.client_id for t in trackers if t.client_id}
    vehicle_ids = {t.vehicle_id for t in trackers if t.vehicle_id}
    tracker_ids = [t.id for t in trackers]

    client_map = {
        c.id: c for c in db.scalars(select(Client).where(Client.id.in_(client_ids))).all()
    } if client_ids else {}
    vehicle_map = {
        v.id: v for v in db.scalars(select(Vehicle).where(Vehicle.id.in_(vehicle_ids))).all()
    } if vehicle_ids else {}

    active_contract_map: dict[int, Contract] = {}
    if tracker_ids:
        # Ordenado por tracker_id, id desc: o primeiro contrato visto por
        # tracker_id (via setdefault) é o de maior id — mesmo "mais recente
        # ativo" que a versão original buscava um por um.
        contracts = db.scalars(
            select(Contract)
            .where(
                Contract.tracker_id.in_(tracker_ids),
                Contract.is_deleted.is_(False),
                Contract.status == 'ativo',
            )
            .order_by(Contract.tracker_id, Contract.id.desc())
        ).all()
        for contract in contracts:
            active_contract_map.setdefault(contract.tracker_id, contract)

    plan_ids = {c.plan_id for c in active_contract_map.values()}
    plan_map = {
        p.id: p for p in db.scalars(select(Plan).where(Plan.id.in_(plan_ids))).all()
    } if plan_ids else {}

    items = [
        _tracker_to_out(
            item, db,
            client_map=client_map,
            vehicle_map=vehicle_map,
            active_contract_map=active_contract_map,
            plan_map=plan_map,
        )
        for item in trackers
    ]
    return {'items': items, 'total': total}


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
    try:
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
    except IntegrityError as exc:
        raise_integrity_conflict(
            db,
            exc,
            _TRACKER_INTEGRITY_MESSAGES,
            sqlite_columns=_TRACKER_SQLITE_CONSTRAINTS,
        )
    db.refresh(tracker)
    return _tracker_to_out(tracker, db)


@router.post('/lote', response_model=TrackerLoteOut)
def create_lote(
    payload: TrackerLoteIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EDIT_ROLES)),
):
    """
    Cadastra vários rastreadores de uma vez (recebimento de remessa).

    Cada IMEI é classificado individualmente — um IMEI repetido ou já
    cadastrado NÃO derruba o lote inteiro, só é ignorado e reportado. Com
    ``simular=True`` nada é gravado, o que permite conferir antes de confirmar.
    """
    comuns = payload.model_dump(exclude={'imeis', 'simular'})

    itens: list[TrackerLoteItem] = []
    vistos: set[str] = set()
    a_criar: list[str] = []

    for bruto in payload.imeis:
        imei = ''.join(filter(str.isdigit, bruto or ''))
        if len(imei) < 5:
            itens.append(TrackerLoteItem(
                imei=bruto.strip(), situacao='invalido',
                motivo='Precisa ter ao menos 5 dígitos',
            ))
            continue
        if imei in vistos:
            itens.append(TrackerLoteItem(
                imei=imei, situacao='repetido_no_lote',
                motivo='Aparece mais de uma vez na lista',
            ))
            continue
        vistos.add(imei)

        existente = db.scalar(
            select(Tracker).where(Tracker.imei == imei, Tracker.is_deleted.is_(False))
        )
        if existente:
            itens.append(TrackerLoteItem(
                imei=imei, situacao='ja_existe', tracker_id=existente.id,
                motivo='Já cadastrado no sistema',
            ))
            continue

        itens.append(TrackerLoteItem(imei=imei, situacao='criado'))
        a_criar.append(imei)

    if payload.simular:
        return TrackerLoteOut(
            simulacao=True,
            total_enviados=len(payload.imeis),
            criados=len(a_criar),
            ignorados=len(payload.imeis) - len(a_criar),
            itens=itens,
        )

    # Cada INSERT usa savepoint: uma disputa de IMEI descoberta somente pela
    # constraint não desfaz os demais itens válidos do lote. Erros de outra
    # natureza continuam revertendo a transação inteira.
    try:
        por_imei: dict[str, Tracker] = {}
        item_by_imei = {
            item.imei: item for item in itens if item.situacao == 'criado'
        }
        for imei in a_criar:
            try:
                with db.begin_nested():
                    tracker = Tracker(**comuns, imei=imei, serial_number=imei)
                    db.add(tracker)
                    db.flush()
                    _register_history(
                        db, tracker,
                        action='created',
                        previous_vehicle_id=None, new_vehicle_id=None,
                        previous_client_id=None, new_client_id=None,
                        previous_status=None,
                        new_status=(
                            tracker.status.value
                            if isinstance(tracker.status, TrackerStatus)
                            else str(tracker.status)
                        ),
                        created_by_user_id=current_user.id,
                        notes='Cadastro em lote',
                    )
                por_imei[imei] = tracker
            except IntegrityError as exc:
                detail = integrity_conflict_detail(
                    exc,
                    _TRACKER_INTEGRITY_MESSAGES,
                    sqlite_columns=_TRACKER_SQLITE_CONSTRAINTS,
                )
                if detail is None:
                    raise
                existing = db.scalar(
                    select(Tracker).where(
                        Tracker.imei == imei,
                        Tracker.is_deleted.is_(False),
                    )
                )
                item = item_by_imei[imei]
                item.situacao = 'ja_existe'
                item.motivo = 'Já cadastrado no sistema'
                item.tracker_id = existing.id if existing else None
        db.commit()
    except Exception:
        db.rollback()
        raise

    for item in itens:
        if item.situacao == 'criado' and item.imei in por_imei:
            item.tracker_id = por_imei[item.imei].id

    return TrackerLoteOut(
        simulacao=False,
        total_enviados=len(payload.imeis),
        criados=len(por_imei),
        ignorados=len(payload.imeis) - len(por_imei),
        itens=itens,
    )


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
        requested_vehicle_id = data.get('vehicle_id')
        if requested_vehicle_id != tracker.vehicle_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    'O vínculo com veículo não pode ser alterado pela edição comum. '
                    'Use o fluxo de vinculação/transferência ou a desinstalação.'
                ),
            )
    if tracker.vehicle_id and 'client_id' in data and data.get('client_id') != tracker.client_id:
        raise HTTPException(
            status_code=409,
            detail='O cliente do rastreador é definido pelo veículo vinculado e não pode ser alterado diretamente.',
        )

    multiportal_changed = has_relevant_changes(tracker, data, TRACKER_MULTIPORTAL_FIELDS)
    for key, value in data.items():
        setattr(tracker, key, value)
    if multiportal_changed:
        invalidate_tracker(tracker, db)

    new_vehicle_id = tracker.vehicle_id
    new_client_id = tracker.client_id
    new_status = tracker.status.value if isinstance(tracker.status, TrackerStatus) else str(tracker.status)

    action = 'updated'
    notes = (
        'Dados enviados à Multiportal atualizados; nova sincronização pendente'
        if multiportal_changed
        else 'Dados gerais atualizados'
    )
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
    try:
        db.commit()
    except IntegrityError as exc:
        raise_integrity_conflict(
            db,
            exc,
            _TRACKER_INTEGRITY_MESSAGES,
            sqlite_columns=_TRACKER_SQLITE_CONSTRAINTS,
        )
    db.refresh(tracker)
    return _tracker_to_out(tracker, db)


@router.delete('/{item_id}')
def delete_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EDIT_ROLES)),
):
    tracker = _get_tracker_or_404(item_id, db)
    if tracker.vehicle_id is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                'Rastreador vinculado não pode ser excluído. '
                'Desinstale-o do veículo antes de remover o cadastro.'
            ),
        )
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
        interveniente_client_id=contract.interveniente_client_id,
        plan_id=contract.plan_id,
        vehicle_id=contract.vehicle_id,
        tracker_id=contract.tracker_id,
        start_date=contract.start_date,
        end_date=contract.end_date,
        status=contract.status,
        billing_day=contract.billing_day,
        payment_method=contract.payment_method,
        notes=contract.notes,
        client_name=client.name if client and not client.is_deleted else None,
        plan_name=plan.name if plan and not plan.is_deleted else None,
        vehicle_plate=vehicle.plate if vehicle and not vehicle.is_deleted else None,
        tracker_identifier=tracker.imei if tracker and not tracker.is_deleted else None,
        monthly_value=float(plan.price) if plan and not plan.is_deleted else None,
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
    """Vincula ou transfere um rastreador e, opcionalmente, cria o contrato."""
    tracker = db.scalar(
        select(Tracker)
        .where(Tracker.id == item_id, Tracker.is_deleted.is_(False))
        .with_for_update()
    )
    if not tracker:
        raise HTTPException(status_code=404, detail='Rastreador não encontrado')
    vehicle = db.scalar(
        select(Vehicle)
        .where(Vehicle.id == payload.vehicle_id, Vehicle.is_deleted.is_(False))
        .with_for_update()
    )
    if not vehicle:
        raise HTTPException(status_code=404, detail='Veículo não encontrado')

    if not vehicle.client_id:
        raise HTTPException(status_code=400, detail='O veículo não possui cliente vinculado.')

    new_client = db.scalar(
        select(Client).where(Client.id == vehicle.client_id, Client.is_deleted.is_(False))
    )
    if not new_client:
        raise HTTPException(status_code=409, detail='O cliente do veículo não está disponível.')

    plan = None
    if payload.plan_id:
        plan = db.get(Plan, payload.plan_id)
        if not plan or plan.is_deleted or not plan.active:
            raise HTTPException(status_code=404, detail='Plano não encontrado ou inativo.')

    interveniente_id = payload.interveniente_client_id
    if interveniente_id == vehicle.client_id:
        interveniente_id = None
    if interveniente_id:
        interveniente = db.get(Client, interveniente_id)
        if not interveniente or interveniente.is_deleted:
            raise HTTPException(status_code=404, detail='Interveniente não encontrado na base de clientes.')

    previous_vehicle_id = tracker.vehicle_id
    previous_client_id = tracker.client_id
    previous_status = tracker.status.value if isinstance(tracker.status, TrackerStatus) else str(tracker.status)
    is_transfer = previous_vehicle_id is not None and previous_vehicle_id != vehicle.id
    if is_transfer and not payload.confirm_transfer:
        raise HTTPException(
            status_code=409,
            detail={
                'code': 'transfer_confirmation_required',
                'message': (
                    'O rastreador já está instalado em outro veículo. '
                    'Confirme explicitamente a transferência para encerrar o vínculo anterior.'
                ),
                'previous_vehicle_id': previous_vehicle_id,
                'destination_vehicle_id': vehicle.id,
            },
        )

    active_contracts = list(
        db.scalars(
            select(Contract)
            .where(
                Contract.tracker_id == tracker.id,
                Contract.status == 'ativo',
                Contract.is_deleted.is_(False),
            )
            .order_by(Contract.id.desc())
            .with_for_update()
        ).all()
    )
    contract_vehicle_ids = {
        contract.vehicle_id for contract in active_contracts if contract.vehicle_id is not None
    }
    if active_contracts and previous_vehicle_id is None:
        raise HTTPException(
            status_code=409,
            detail={
                'code': 'orphan_active_contract',
                'message': (
                    'O rastreador está sem veículo local, mas ainda possui contrato ativo. '
                    'Reconcilie esse contrato antes de criar outro vínculo.'
                ),
                'contract_ids': [contract.id for contract in active_contracts],
            },
        )
    if previous_vehicle_id is not None and any(
        vehicle_id != previous_vehicle_id for vehicle_id in contract_vehicle_ids
    ):
        raise HTTPException(
            status_code=409,
            detail={
                'code': 'inconsistent_active_contract_assignment',
                'message': (
                    'Há contrato ativo deste rastreador apontando para outro veículo. '
                    'Reconcilie os dados antes de transferir.'
                ),
                'contract_ids': [contract.id for contract in active_contracts],
            },
        )
    from app.core.timezone import hoje
    if is_transfer and payload.start_date > hoje():
        raise HTTPException(
            status_code=422,
            detail='A data da transferência não pode estar no futuro.',
        )
    if any(payload.start_date < contract.start_date for contract in active_contracts):
        raise HTTPException(status_code=422, detail='A transferência não pode ser anterior ao início do contrato atual.')

    if previous_vehicle_id == vehicle.id and not payload.plan_id:
        return {
            'tracker': _tracker_to_out(tracker, db),
            'contract': None,
            'message': f'Rastreador já está vinculado ao veículo {vehicle.plate}.',
        }
    if previous_vehicle_id == vehicle.id and payload.plan_id and active_contracts:
        raise HTTPException(status_code=409, detail='Já existe contrato ativo para este rastreador e veículo.')

    old_vehicle = None
    lifecycle = None
    if is_transfer:
        old_vehicle = db.scalar(
            select(Vehicle).where(Vehicle.id == previous_vehicle_id).with_for_update()
        )
        if not old_vehicle:
            raise HTTPException(
                status_code=409,
                detail='O veículo anterior não existe mais; reconcilie o vínculo antes de transferir.',
            )
        try:
            lifecycle = transfer_tracker_assignment(
                tracker=tracker,
                old_vehicle=old_vehicle,
                new_vehicle=vehicle,
                new_client=new_client,
            )
        except LifecycleSyncError as exc:
            add_lifecycle_logs(db, exc.calls)
            if exc.compensation_failed:
                apply_tracker_integration_result(tracker, exc.calls, status='erro')
            db.commit()
            raise HTTPException(
                status_code=exc.http_status,
                detail={
                    'code': 'multiportal_transfer_failed',
                    'message': str(exc),
                    'reconciliation_required': exc.compensation_failed,
                },
            ) from exc
    else:
        lifecycle = LifecycleResult()

    tracker.vehicle_id = vehicle.id
    tracker.client_id = vehicle.client_id
    tracker.serial_number = tracker.serial_number or tracker.imei
    if tracker.status == TrackerStatus.STOCK:
        tracker.status = TrackerStatus.INSTALLED
        tracker.install_date = tracker.install_date or date.today()

    # Reinstalar um rastreador reativa o veículo: sem isto, um veículo antes
    # RETIRADO ficaria travado nesse status e não poderia ser desinstalado de novo.
    if vehicle.status == VehicleStatus.REMOVED:
        vehicle.status = VehicleStatus.ACTIVE
        vehicle.uninstalled_at = None

    if is_transfer:
        for old_contract in active_contracts:
            old_contract.status = 'cancelado'
            old_contract.end_date = payload.start_date
            transfer_note = f'Transferido para o veículo {vehicle.plate} em {payload.start_date.strftime("%d/%m/%Y")}'
            old_contract.notes = (
                f'{old_contract.notes}\n{transfer_note}'.strip()
                if old_contract.notes else transfer_note
            )
        other_tracker = db.scalar(
            select(Tracker.id).where(
                Tracker.vehicle_id == old_vehicle.id,
                Tracker.id != tracker.id,
                Tracker.is_deleted.is_(False),
            )
        )
        if not other_tracker and old_vehicle.status == VehicleStatus.ACTIVE:
            old_vehicle.status = VehicleStatus.NO_TRACKER

    apply_tracker_integration_result(
        tracker,
        lifecycle.calls,
        status='sincronizado' if lifecycle.managed_externally else 'pendente',
    )
    if not lifecycle.managed_externally:
        # Vínculo criado sem passar pelo Multiportal (integração desligada ou
        # sem referência externa ainda): entra na fila para o worker levar o
        # cadastro completo quando o provedor estiver disponível, em vez de
        # ficar 'pendente' esperando alguém clicar em sincronizar.
        enqueue_full_sync(db, tracker.id, reason='vínculo criado', flush=False)

    _register_history(
        db, tracker,
        action='transferred' if is_transfer else 'linked',
        previous_vehicle_id=previous_vehicle_id,
        new_vehicle_id=vehicle.id,
        previous_client_id=previous_client_id,
        new_client_id=vehicle.client_id,
        previous_status=previous_status,
        new_status=tracker.status.value if isinstance(tracker.status, TrackerStatus) else str(tracker.status),
        created_by_user_id=current_user.id,
        notes=(f'Transferido para o veículo {vehicle.plate}' if is_transfer else f'Vinculado ao veículo {vehicle.plate}')
        + (f' com plano #{payload.plan_id}' if payload.plan_id else ''),
    )

    contract_out: ContractOut | None = None
    if plan:
        # Dia de vencimento: usa payload > cliente > dia do mês de início (cap 28)
        client_billing_day = getattr(new_client, 'billing_day', None)
        billing_day = payload.billing_day or client_billing_day or (
            payload.start_date.day if payload.start_date.day <= 28 else 28
        )

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

    commit_with_compensation(
        db,
        lifecycle_calls=lifecycle.calls,
        should_compensate=is_transfer and lifecycle.managed_externally and bool(old_vehicle),
        run_compensation=lambda: compensate_successful_transfer(
            tracker=tracker,
            old_vehicle=old_vehicle,
            new_vehicle=vehicle,
        ),
    )
    db.refresh(tracker)

    return {
        'tracker': _tracker_to_out(tracker, db),
        'contract': contract_out,
        'message': (
            f'Rastreador transferido para o veículo {vehicle.plate}'
            if is_transfer else f'Rastreador vinculado ao veículo {vehicle.plate}'
        ) + (' com contrato criado.' if contract_out else '.'),
        'previous_contracts_closed': len(active_contracts) if is_transfer else 0,
        'multiportal_synchronized': lifecycle.managed_externally,
    }
