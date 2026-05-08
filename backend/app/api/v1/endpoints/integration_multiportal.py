from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from uuid import uuid4

from app.api.deps import require_roles
from app.db.session import get_db
from app.core.config import settings
from app.models.client import Client
from app.models.integration_log import IntegrationLog
from app.models.tracker import Tracker
from app.models.user import User
from app.models.vehicle import Vehicle
from app.models.enums import UserRole
from app.schemas.integration import (
    IntegrationFlowOut,
    IntegrationLogOut,
    IntegrationStatusOut,
    ManufacturerOut,
)
from app.services.multiportal import CallResult, MultiportalError, multiportal_service
from app.services.multiportal_messages import interpret_multiportal_response

router = APIRouter()
EDIT_ROLES = (UserRole.ADMIN, UserRole.OPERATIONAL)
VIEW_ROLES = (UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL)


def _serialize_step(result: CallResult) -> dict:
    return {**result.as_dict(), **interpret_multiportal_response(result.operation, result.status_code, result.status_description)}


def _serialize_log(entry: IntegrationLog) -> IntegrationLogOut:
    return IntegrationLogOut(
        id=entry.id,
        provider=entry.provider,
        batch_id=entry.batch_id,
        entity_type=entry.entity_type,
        entity_id=entry.entity_id,
        operation=entry.operation,
        transaction_id=entry.transaction_id,
        success=entry.success,
        response_code=entry.response_code,
        response_description=entry.response_description,
        created_at=entry.created_at,
        **interpret_multiportal_response(entry.operation, entry.response_code, entry.response_description),
    )


def _require_tracker(item_id: int, db: Session) -> Tracker:
    tracker = db.scalar(select(Tracker).where(Tracker.id == item_id, Tracker.is_deleted.is_(False)))
    if not tracker:
        raise HTTPException(status_code=404, detail='Rastreador não encontrado')
    return tracker


def _require_vehicle(item_id: int, db: Session) -> Vehicle:
    vehicle = db.scalar(select(Vehicle).where(Vehicle.id == item_id, Vehicle.is_deleted.is_(False)))
    if not vehicle:
        raise HTTPException(status_code=404, detail='Veículo não encontrado')
    return vehicle


def _require_client(item_id: int, db: Session) -> Client:
    client = db.scalar(select(Client).where(Client.id == item_id, Client.is_deleted.is_(False)))
    if not client:
        raise HTTPException(status_code=404, detail='Cliente não encontrado')
    return client


def _linked_client_user(client_id: int, db: Session) -> User | None:
    return db.scalar(select(User).where(User.client_id == client_id, User.role == UserRole.CLIENT, User.is_deleted.is_(False)))


def _save_log(
    *,
    db: Session,
    batch_id: str,
    entity_type: str,
    entity_id: int | None,
    result: CallResult,
    request_payload: dict | None = None,
) -> None:
    db.add(
        IntegrationLog(
            provider='multiportal',
            batch_id=batch_id,
            entity_type=entity_type,
            entity_id=entity_id,
            operation=result.operation,
            transaction_id=result.transaction_id,
            success=result.success,
            response_code=result.status_code,
            response_description=result.status_description,
            request_payload=request_payload,
            response_payload=result.response_payload,
        )
    )


@router.get('/status', response_model=IntegrationStatusOut)
def integration_status(_: object = Depends(require_roles(*VIEW_ROLES))):
    credentials_configured = bool(settings.multiportal_id and settings.multiportal_password and settings.multiportal_wsdl_url)
    return IntegrationStatusOut(
        enabled=multiportal_service.enabled,
        wsdl_url=settings.multiportal_wsdl_url if credentials_configured else None,
        credentials_configured=credentials_configured,
        group_codes_configured=bool(settings.multiportal_group_codes),
    )


@router.get('/manufacturers', response_model=list[ManufacturerOut])
def list_manufacturers(_: object = Depends(require_roles(*VIEW_ROLES))):
    try:
        return [ManufacturerOut(code=item['code'], description=item['description']) for item in multiportal_service.list_manufacturers()]
    except MultiportalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get('/logs', response_model=list[IntegrationLogOut])
def list_logs(
    tracker_id: int | None = None,
    client_id: int | None = None,
    vehicle_id: int | None = None,
    success: bool | None = None,
    limit: int = Query(default=100, le=300),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    stmt = select(IntegrationLog).where(IntegrationLog.provider == 'multiportal')
    if tracker_id:
        tracker = _require_tracker(tracker_id, db)
        related_filters = [IntegrationLog.entity_type == 'tracker', IntegrationLog.entity_id == tracker_id]
        conditions = [(IntegrationLog.entity_type == 'tracker') & (IntegrationLog.entity_id == tracker_id)]
        if tracker.vehicle_id:
            conditions.append((IntegrationLog.entity_type == 'vehicle') & (IntegrationLog.entity_id == tracker.vehicle_id))
            vehicle = db.get(Vehicle, tracker.vehicle_id)
            if vehicle and vehicle.client_id:
                conditions.append((IntegrationLog.entity_type == 'client') & (IntegrationLog.entity_id == vehicle.client_id))
        elif tracker.client_id:
            conditions.append((IntegrationLog.entity_type == 'client') & (IntegrationLog.entity_id == tracker.client_id))
        stmt = stmt.where(or_(*conditions))
    elif client_id:
        stmt = stmt.where(IntegrationLog.entity_type == 'client', IntegrationLog.entity_id == client_id)
    elif vehicle_id:
        stmt = stmt.where(IntegrationLog.entity_type == 'vehicle', IntegrationLog.entity_id == vehicle_id)
    if success is not None:
        stmt = stmt.where(IntegrationLog.success == success)
    stmt = stmt.order_by(IntegrationLog.id.desc()).limit(limit)
    items = db.scalars(stmt).all()
    return [_serialize_log(item) for item in items]


@router.post('/trackers/{tracker_id}/reprocess-last-failed', response_model=IntegrationFlowOut)
def reprocess_last_failed(tracker_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(*EDIT_ROLES))):
    tracker = _require_tracker(tracker_id, db)
    vehicle = _require_vehicle(tracker.vehicle_id, db) if tracker.vehicle_id else None
    local_client = _require_client(vehicle.client_id, db) if vehicle and vehicle.client_id else (_require_client(tracker.client_id, db) if tracker.client_id else None)
    linked_user = _linked_client_user(local_client.id, db) if local_client else None

    conditions = [(IntegrationLog.entity_type == 'tracker') & (IntegrationLog.entity_id == tracker_id)]
    if vehicle:
        conditions.append((IntegrationLog.entity_type == 'vehicle') & (IntegrationLog.entity_id == vehicle.id))
    if local_client:
        conditions.append((IntegrationLog.entity_type == 'client') & (IntegrationLog.entity_id == local_client.id))

    failed_log = db.scalar(
        select(IntegrationLog)
        .where(IntegrationLog.provider == 'multiportal', IntegrationLog.success.is_(False), or_(*conditions))
        .order_by(IntegrationLog.id.desc())
    )
    if not failed_log:
        raise HTTPException(status_code=404, detail='Não existe etapa com erro para reprocessar neste rastreador.')

    try:
        if failed_log.operation == 'sincronizaCliente':
            if not local_client:
                raise HTTPException(status_code=400, detail='Rastreador sem cliente vinculado para reprocessar cliente.')
            steps = [multiportal_service.sync_client(local_client, linked_user)]
        elif failed_log.operation == 'sincronizaUsuario':
            if not local_client or not linked_user:
                raise HTTPException(status_code=400, detail='Cliente sem usuário de portal vinculado para reprocessar.')
            steps = [multiportal_service.sync_user(local_client, linked_user)]
        elif failed_log.operation == 'sincronizaVeiculo':
            if not vehicle:
                raise HTTPException(status_code=400, detail='Rastreador sem veículo vinculado para reprocessar.')
            steps = [multiportal_service.sync_vehicle(vehicle)]
        elif failed_log.operation == 'sincronizaEquipamento':
            steps = [multiportal_service.sync_equipment(tracker)]
        elif failed_log.operation == 'vinculoVeiculoCliente':
            if not vehicle or not local_client:
                raise HTTPException(status_code=400, detail='Faltam veículo ou cliente para reprocessar o vínculo.')
            steps = [multiportal_service.link_vehicle_client(vehicle, local_client)]
        elif failed_log.operation == 'vinculoEquipamentoVeiculo':
            if not vehicle:
                raise HTTPException(status_code=400, detail='Falta vínculo com veículo para reprocessar o equipamento.')
            steps = [multiportal_service.link_equipment_vehicle(tracker, vehicle)]
        else:
            raise HTTPException(status_code=400, detail='A última etapa com erro não suporta reprocessamento automático.')
    except MultiportalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    batch_id = uuid4().hex
    for result in steps:
        entity_type = 'tracker'
        entity_id = tracker.id
        if result.operation == 'sincronizaCliente' and local_client:
            entity_type, entity_id = 'client', local_client.id
        elif result.operation in {'sincronizaVeiculo', 'vinculoVeiculoCliente'} and vehicle:
            entity_type, entity_id = 'vehicle', vehicle.id
        _save_log(db=db, batch_id=batch_id, entity_type=entity_type, entity_id=entity_id, result=result)

    overall_success = all(step.success for step in steps)
    tracker.integration_status = 'sincronizado' if overall_success else 'erro'
    tracker.integration_last_code = steps[-1].status_code if steps else None
    tracker.integration_last_description = steps[-1].status_description if steps else None
    tracker.integration_last_transaction_id = steps[-1].transaction_id if steps else None
    db.commit()
    return IntegrationFlowOut(provider='multiportal', entity_type='tracker', entity_id=tracker.id, overall_success=overall_success, steps=[_serialize_step(step) for step in steps])


@router.post('/clients/{client_id}/sync', response_model=IntegrationFlowOut)
def sync_client(client_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(*EDIT_ROLES))):
    local_client = _require_client(client_id, db)
    linked_user = _linked_client_user(local_client.id, db)
    batch_id = uuid4().hex
    try:
        result = multiportal_service.sync_client(local_client, linked_user)
    except MultiportalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save_log(db=db, batch_id=batch_id, entity_type='client', entity_id=local_client.id, result=result)
    db.commit()
    return IntegrationFlowOut(provider='multiportal', entity_type='client', entity_id=local_client.id, overall_success=result.success, steps=[_serialize_step(result)])


@router.post('/vehicles/{vehicle_id}/sync', response_model=IntegrationFlowOut)
def sync_vehicle(vehicle_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(*EDIT_ROLES))):
    vehicle = _require_vehicle(vehicle_id, db)
    batch_id = uuid4().hex
    try:
        result = multiportal_service.sync_vehicle(vehicle)
    except MultiportalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save_log(db=db, batch_id=batch_id, entity_type='vehicle', entity_id=vehicle.id, result=result)
    db.commit()
    return IntegrationFlowOut(provider='multiportal', entity_type='vehicle', entity_id=vehicle.id, overall_success=result.success, steps=[_serialize_step(result)])


@router.post('/trackers/{tracker_id}/sync-equipment', response_model=IntegrationFlowOut)
def sync_equipment(tracker_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(*EDIT_ROLES))):
    tracker = _require_tracker(tracker_id, db)
    batch_id = uuid4().hex
    try:
        result = multiportal_service.sync_equipment(tracker)
    except MultiportalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save_log(db=db, batch_id=batch_id, entity_type='tracker', entity_id=tracker.id, result=result)
    tracker.integration_status = 'sincronizado' if result.success else 'erro'
    tracker.integration_last_code = result.status_code
    tracker.integration_last_description = result.status_description
    tracker.integration_last_transaction_id = result.transaction_id
    db.commit()
    return IntegrationFlowOut(provider='multiportal', entity_type='tracker', entity_id=tracker.id, overall_success=result.success, steps=[_serialize_step(result)])


@router.post('/trackers/{tracker_id}/sync-flow', response_model=IntegrationFlowOut)
def sync_flow(tracker_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(*EDIT_ROLES))):
    tracker = _require_tracker(tracker_id, db)
    if not tracker.vehicle_id:
        raise HTTPException(status_code=400, detail='Rastreador precisa estar vinculado a um veículo para sincronizar o fluxo completo.')
    vehicle = _require_vehicle(tracker.vehicle_id, db)
    local_client = _require_client(vehicle.client_id, db)
    linked_user = _linked_client_user(local_client.id, db)
    batch_id = uuid4().hex
    try:
        steps = multiportal_service.full_sync_for_tracker(tracker=tracker, vehicle=vehicle, local_client=local_client, linked_user=linked_user)
    except MultiportalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    for result in steps:
        entity_type = 'tracker'
        entity_id = tracker.id
        if result.operation == 'sincronizaCliente':
            entity_type, entity_id = 'client', local_client.id
        elif result.operation in {'sincronizaVeiculo', 'vinculoVeiculoCliente'}:
            entity_type, entity_id = 'vehicle', vehicle.id
        _save_log(db=db, batch_id=batch_id, entity_type=entity_type, entity_id=entity_id, result=result)

    overall_success = all(step.success for step in steps)
    tracker.integration_status = 'sincronizado' if overall_success else 'erro'
    tracker.integration_last_code = steps[-1].status_code if steps else None
    tracker.integration_last_description = steps[-1].status_description if steps else None
    tracker.integration_last_transaction_id = steps[-1].transaction_id if steps else None
    db.commit()
    return IntegrationFlowOut(
        provider='multiportal',
        entity_type='tracker',
        entity_id=tracker.id,
        overall_success=overall_success,
        steps=[_serialize_step(step) for step in steps],
    )
