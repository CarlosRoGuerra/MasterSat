from __future__ import annotations

from collections.abc import Mapping, Set as AbstractSet
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.tracker import Tracker
from app.models.vehicle import Vehicle


# Estes conjuntos espelham os dados consumidos pelos builders em
# app.services.multiportal. Campos exclusivamente locais (notas, dados fiscais,
# preço etc.) não devem criar uma falsa pendência de integração.
CLIENT_MULTIPORTAL_FIELDS = frozenset({
    'name',
    'trade_name',
    'cpf_cnpj',
    'type',
    'email',
    'phone',
    'contacts',
})

VEHICLE_MULTIPORTAL_FIELDS = frozenset({
    'client_id',  # muda o vínculo veículo-cliente no provedor
    'chassis',
    'plate',
    'type',
    'contract_number',
    'brand',
    'model',
    'color',
    'year',
    'model_year',
    'manufacture_year',
    'renavam',
    'fipe_code',
})

TRACKER_MULTIPORTAL_FIELDS = frozenset({
    'imei',
    'serial_number',
    'external_manufacturer_id',
    'firmware',
    'sim_number',
    'sim_iccid',
    'sim_status',
    'carrier',
    'ip_address',
    'port',
    'install_location',
    'chip_type',
    'equipment_type',
    'communication_type',
    'install_date',
})


def has_relevant_changes(
    instance: object,
    updates: Mapping[str, Any],
    relevant_fields: AbstractSet[str],
) -> bool:
    """Retorna True somente quando um valor enviado ao provedor realmente muda."""
    return any(
        field in updates and getattr(instance, field, None) != updates[field]
        for field in relevant_fields
    )


def _mark_pending(trackers: list[Tracker], db: Session | None = None, reason: str | None = None) -> int:
    """Marca os rastreadores como pendentes e os enfileira para sincronizar.

    O enfileiramento acontece na MESMA transação da alteração do dado (padrão
    outbox): ou as duas coisas valem, ou nenhuma. Marcar como pendente sem
    enfileirar deixaria o registro esperando um clique manual — que é o
    problema que a fila existe para resolver.
    """
    from app.services.multiportal_outbox import enqueue_full_sync

    changed = 0
    for tracker in trackers:
        if tracker.integration_status != 'pendente':
            tracker.integration_status = 'pendente'
            changed += 1
        # Enfileira mesmo se já estava 'pendente': o dado mudou de novo, e
        # enqueue_full_sync é idempotente por rastreador.
        if db is not None and tracker.vehicle_id:
            enqueue_full_sync(db, tracker.id, reason=reason, flush=False)
    return changed


def invalidate_client_trackers(db: Session, client_id: int) -> int:
    """Invalida todos os fluxos completos que dependem dos dados do cliente."""
    vehicle_ids = select(Vehicle.id).where(
        Vehicle.client_id == client_id,
        Vehicle.is_deleted.is_(False),
    )
    trackers = list(
        db.scalars(
            select(Tracker).where(
                Tracker.is_deleted.is_(False),
                or_(
                    Tracker.client_id == client_id,
                    Tracker.vehicle_id.in_(vehicle_ids),
                ),
            )
        ).all()
    )
    return _mark_pending(trackers, db, reason='cliente alterado')


def invalidate_vehicle_trackers(db: Session, vehicle_id: int) -> int:
    """Invalida todos os rastreadores vinculados ao veículo alterado."""
    trackers = list(
        db.scalars(
            select(Tracker).where(
                Tracker.vehicle_id == vehicle_id,
                Tracker.is_deleted.is_(False),
            )
        ).all()
    )
    return _mark_pending(trackers, db, reason='veículo alterado')


def invalidate_tracker(tracker: Tracker, db: Session | None = None) -> bool:
    """Marca o próprio equipamento para nova sincronização e o enfileira."""
    from app.services.multiportal_outbox import enqueue_full_sync

    ja_pendente = tracker.integration_status == 'pendente'
    tracker.integration_status = 'pendente'
    if db is not None and tracker.vehicle_id:
        enqueue_full_sync(db, tracker.id, reason='rastreador alterado', flush=False)
    return not ja_pendente
