from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.billing import Billing
from app.models.billing_charge_item import BillingChargeItem
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.models.vehicle import Vehicle
from app.services.billing_closure.shared import _apply_client_scope
from app.services.financial import InstallmentSplitError, charge_item_payer_client_id, split_amount_in_installments


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
    if filter_type == 'client' and client_id is not None:
        query = _apply_client_scope(query, ClientChargeItem.client_id, 'all', None)
        query = query.outerjoin(Contract, Contract.id == ClientChargeItem.contract_id).filter(
            func.coalesce(
                Contract.interveniente_client_id, Contract.client_id, ClientChargeItem.client_id,
            ) == client_id
        )
    else:
        query = _apply_client_scope(query, ClientChargeItem.client_id, filter_type, client_id)
    items = query.order_by(ClientChargeItem.id.asc()).all()

    candidate_items = [
        item for item in items if not (exclude_ids and item.id in exclude_ids)
    ]
    billing_counts = _effective_billing_counts_bulk(db, [item.id for item in candidate_items])

    contract_ids = {item.contract_id for item in candidate_items if item.contract_id}
    vehicle_ids = {item.vehicle_id for item in candidate_items if item.vehicle_id}
    contract_map = {
        c.id: c for c in db.scalars(select(Contract).where(Contract.id.in_(contract_ids))).all()
    } if contract_ids else {}
    client_ids = {item.client_id for item in candidate_items}
    client_ids.update(
        contract.interveniente_client_id or contract.client_id
        for contract in contract_map.values()
    )
    client_map = {
        c.id: c for c in db.scalars(select(Client).where(Client.id.in_(client_ids))).all()
    } if client_ids else {}
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
        payer_id = charge_item_payer_client_id(db, item)
        payer = client_map.get(payer_id)
        vehicle = vehicle_map.get(item.vehicle_id) if item.vehicle_id else None
        remaining = installments - billing_count
        # Mesma divisão que generate_item_billings vai gravar: a prévia não
        # pode prometer um total diferente do que o fechamento cria.
        try:
            parcelas = split_amount_in_installments(item.total_amount, installments)
        except InstallmentSplitError as exc:
            raise ValueError(
                f'Lançamento #{item.id} ({item.title}): {exc} Ajuste o lançamento antes do fechamento.'
            ) from exc
        per_installment = parcelas[0]
        restantes = parcelas[billing_count:]

        result.append({
            'type': 'servico',
            'item_id': item.id,
            'client_id': item.client_id,
            'client_name': client.name if client else f'Cliente #{item.client_id}',
            'payer_client_id': payer_id,
            'payer_name': payer.name if payer else f'Responsável financeiro #{payer_id}',
            'client_type': client.type if client else 'pf',
            'vehicle_plate': vehicle.plate if vehicle else None,
            'title': item.title,
            'quantity': item.quantity,
            'unit_price': float(item.unit_price),
            'installment_count': installments,
            'generated_count': billing_count,
            'remaining_installments': remaining,
            'per_installment_amount': float(per_installment),
            'total_remaining': float(sum(restantes, Decimal('0.00'))),
            'start_date': item.start_date,
        })

    return result
