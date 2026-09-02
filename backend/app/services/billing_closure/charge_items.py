from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billing import Billing
from app.models.billing_charge_item import BillingChargeItem
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.models.vehicle import Vehicle
from app.services.billing_closure.shared import _apply_client_scope


def _effective_billing_counts_bulk(db: Session, item_ids: list[int]) -> dict[int, int]:
    """Mesma regra de `charge_item_effective_billing_count` (financial.py), mas
    para vários itens de uma vez — evita 1 query por item nos loops de
    fechamento (`_pending_charge_items`), que rodam sobre todos os serviços
    avulsos pendentes do mês.
    """
    if not item_ids:
        return {}
    billing_ids_by_item: dict[int, set[int]] = defaultdict(set)
    direct_rows = db.query(Billing.item_id, Billing.id).filter(
        Billing.is_deleted.is_(False),
        Billing.status != BillingStatus.CANCELED,
        Billing.item_id.in_(item_ids),
    ).all()
    for item_id, billing_id in direct_rows:
        billing_ids_by_item[item_id].add(billing_id)
    assoc_rows = (
        db.query(BillingChargeItem.item_id, BillingChargeItem.billing_id)
        .join(Billing, Billing.id == BillingChargeItem.billing_id)
        .filter(
            BillingChargeItem.item_id.in_(item_ids),
            Billing.is_deleted.is_(False),
            Billing.status != BillingStatus.CANCELED,
        )
        .all()
    )
    for item_id, billing_id in assoc_rows:
        billing_ids_by_item[item_id].add(billing_id)
    return {item_id: len(ids) for item_id, ids in billing_ids_by_item.items()}


def _pending_charge_items(
    db: Session,
    reference_month: date,
    exclude_ids: set[int] | None = None,
    filter_type: str = 'all',
    client_id: int | None = None,
) -> list[dict]:
    """
    Retorna ClientChargeItems ativos cujos billings ainda não foram totalmente gerados
    e cujo start_date é anterior ao final do mês de referência.
    exclude_ids: item_ids já embutidos em cobranças combinadas de primeiro mês.
    """
    if reference_month.month == 12:
        month_end = date(reference_month.year + 1, 1, 1)
    else:
        month_end = date(reference_month.year, reference_month.month + 1, 1)

    query = (
        db.query(ClientChargeItem)
        .filter(
            ClientChargeItem.is_deleted.is_(False),
            ClientChargeItem.active.is_(True),
            ClientChargeItem.start_date < month_end,
        )
    )
    items = _apply_client_scope(
        query, ClientChargeItem.client_id, filter_type, client_id,
    ).order_by(ClientChargeItem.id.asc()).all()

    candidate_items = [
        item for item in items if not (exclude_ids and item.id in exclude_ids)
    ]
    billing_counts = _effective_billing_counts_bulk(db, [item.id for item in candidate_items])

    client_ids = {item.client_id for item in candidate_items}
    contract_ids = {item.contract_id for item in candidate_items if item.contract_id}
    vehicle_ids = {item.vehicle_id for item in candidate_items if item.vehicle_id}
    client_map = {
        c.id: c for c in db.scalars(select(Client).where(Client.id.in_(client_ids))).all()
    } if client_ids else {}
    contract_map = {
        c.id: c for c in db.scalars(select(Contract).where(Contract.id.in_(contract_ids))).all()
    } if contract_ids else {}
    vehicle_map = {
        v.id: v for v in db.scalars(select(Vehicle).where(Vehicle.id.in_(vehicle_ids))).all()
    } if vehicle_ids else {}

    result = []
    for item in candidate_items:
        billing_count = billing_counts.get(item.id, 0)

        installments = max(int(item.installment_count or 1), 1)
        if billing_count >= installments:
            continue

        if item.contract_id:
            item_contract = contract_map.get(item.contract_id)
            if not item_contract or item_contract.is_deleted:
                raise ValueError(
                    f'Lançamento #{item.id} está ativo, mas referencia contrato '
                    f'removido #{item.contract_id}. Reconcilie o lançamento antes do fechamento.'
                )

        client = client_map.get(item.client_id)
        vehicle = vehicle_map.get(item.vehicle_id) if item.vehicle_id else None
        remaining = installments - billing_count
        total = Decimal(str(item.total_amount))
        per_installment = (total / installments).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        result.append({
            'type': 'servico',
            'item_id': item.id,
            'client_id': item.client_id,
            'client_name': client.name if client else f'Cliente #{item.client_id}',
            'client_type': client.type if client else 'pf',
            'vehicle_plate': vehicle.plate if vehicle else None,
            'title': item.title,
            'installment_count': installments,
            'generated_count': billing_count,
            'remaining_installments': remaining,
            'per_installment_amount': float(per_installment),
            'total_remaining': float(per_installment * remaining),
            'start_date': item.start_date,
        })

    return result
