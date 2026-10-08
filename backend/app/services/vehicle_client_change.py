from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timezone import hoje
from app.models.audit_log import AuditLog
from app.models.billing import Billing
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.tracker import Tracker
from app.models.tracker_history import TrackerHistory
from app.models.user import User
from app.models.vehicle import Vehicle
from app.services.multiportal_sync_state import invalidate_vehicle_trackers


def change_vehicle_client(
    db: Session,
    vehicle: Vehicle,
    client_id: int,
    user: User,
    *,
    change_payer: bool = False,
    interveniente_client_id: int | None = None,
    method: str = 'POST',
    path: str,
) -> tuple[list[int], list[int]]:
    """Troca local e atômica; não consulta o provedor nem desfaz a instalação.

    O chamador trava o veículo e comita. Contratos são travados antes de
    alterar o pagador, como no fechamento. Títulos legados sem snapshot
    recebem o pagador anterior antes de a referência do contrato mudar.
    """
    client = db.get(Client, client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail='Cliente não encontrado')
    if interveniente_client_id == client_id:
        interveniente_client_id = None
    if change_payer and interveniente_client_id is not None:
        payer = db.get(Client, interveniente_client_id)
        if not payer or payer.is_deleted:
            raise HTTPException(status_code=404, detail='Interveniente não encontrado')

    contracts = list(db.scalars(
        select(Contract).where(
            Contract.vehicle_id == vehicle.id,
            Contract.status == 'ativo',
            Contract.is_deleted.is_(False),
        ).order_by(Contract.id).with_for_update().execution_options(populate_existing=True)
    ).all())
    trackers = list(db.scalars(
        select(Tracker).where(
            Tracker.vehicle_id == vehicle.id,
            Tracker.is_deleted.is_(False),
        ).order_by(Tracker.id).with_for_update().execution_options(populate_existing=True)
    ).all())
    tracker_ids = {tracker.id for tracker in trackers}
    if any(c.tracker_id and c.tracker_id not in tracker_ids for c in contracts):
        raise HTTPException(
            status_code=409,
            detail='Existe contrato ativo com rastreador fora deste veículo. Corrija o vínculo antes de trocar o cliente.',
        )

    previous_client_id = vehicle.client_id
    changed_contracts = [c for c in contracts if (
        c.client_id != client_id
        or (change_payer and c.interveniente_client_id != interveniente_client_id)
    )]
    changed_ids = [c.id for c in changed_contracts]
    previous_contracts = {
        c.id: {'client_id': c.client_id, 'interveniente_client_id': c.interveniente_client_id}
        for c in changed_contracts
    }
    if changed_ids:
        previous_payer_ids = {c.interveniente_client_id for c in changed_contracts if c.interveniente_client_id}
        available_previous_payers = set(db.scalars(select(Client.id).where(
            Client.id.in_(previous_payer_ids), Client.is_deleted.is_(False),
        )).all()) if previous_payer_ids else set()
        legacy_billings = db.scalars(
            select(Billing).where(
                Billing.contract_id.in_(changed_ids),
                Billing.payer_client_id.is_(None),
            ).order_by(Billing.id).with_for_update().execution_options(populate_existing=True)
        ).all()
        for billing in legacy_billings:
            old = previous_contracts[billing.contract_id]
            previous_payer = old['interveniente_client_id']
            billing.payer_client_id = previous_payer if previous_payer in available_previous_payers else billing.client_id

        # As parcelas já geradas mantêm o cliente/pagador de seu Billing.
        # O cadastro do serviço acompanha o contrato para as parcelas futuras.
        charge_items = db.scalars(
            select(ClientChargeItem).where(
                ClientChargeItem.contract_id.in_(changed_ids),
                ClientChargeItem.active.is_(True),
                ClientChargeItem.is_deleted.is_(False),
            ).order_by(ClientChargeItem.id).with_for_update().execution_options(populate_existing=True)
        ).all()
        for item in charge_items:
            item.client_id = client_id

    vehicle.client_id = client_id
    for contract in contracts:
        contract.client_id = client_id
        if change_payer:
            contract.interveniente_client_id = interveniente_client_id

    changed_trackers = []
    for tracker in trackers:
        old_client_id = tracker.client_id
        if old_client_id == client_id:
            continue
        tracker.client_id = client_id
        changed_trackers.append(tracker.id)
        db.add(TrackerHistory(
            tracker_id=tracker.id,
            action='client_changed',
            previous_vehicle_id=vehicle.id,
            new_vehicle_id=vehicle.id,
            previous_client_id=old_client_id,
            new_client_id=client_id,
            previous_status=tracker.status.value,
            new_status=tracker.status.value,
            event_date=hoje(),
            created_by_user_id=user.id,
            notes=f'Cliente da placa {vehicle.plate} alterado para {client.name}; instalação mantida.',
        ))
    if previous_client_id != client_id or changed_trackers:
        # Somente intenção de envio na fila local: nenhuma chamada externa
        # participa desta transação, mesmo com a Multiportal indisponível.
        invalidate_vehicle_trackers(db, vehicle.id)
    if previous_client_id != client_id or changed_ids or changed_trackers:
        db.add(AuditLog(
            user_id=user.id, user_name=user.name, user_role=user.role.value,
            method=method, path=path, entity_type='vehicle', entity_id=vehicle.id,
            status_code=200,
            description=json.dumps({
                'action': 'change_client_payer', 'plate': vehicle.plate,
                'previous_client_id': previous_client_id, 'client_id': client_id,
                'previous_contracts': previous_contracts,
                'change_payer': change_payer,
                'interveniente_client_id': interveniente_client_id if change_payer else None,
            }, ensure_ascii=False),
        ))
    return changed_ids, changed_trackers
