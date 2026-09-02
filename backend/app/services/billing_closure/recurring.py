from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.plan import Plan
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.services.financial import (
    _quantize_amount,
    charge_item_effective_billing_count,
    normalize_due_date,
)


def _billing_due_in_month(contract: Contract, plan: Plan, reference_month: date) -> date | None:
    interval = max(int(getattr(plan, 'billing_interval_months', 1) or 1), 1)
    billing_day = contract.billing_day or 1
    months_since_start = (
        (reference_month.year - contract.start_date.year) * 12
        + (reference_month.month - contract.start_date.month)
    )
    if months_since_start < 0:
        return None
    cycle = months_since_start // interval
    due_date = normalize_due_date(contract.start_date, cycle, billing_day, interval)
    # Never produce a due date that predates the contract start (billing_day < start day)
    if due_date < contract.start_date:
        return None
    if due_date.year == reference_month.year and due_date.month == reference_month.month:
        return due_date
    return None


def _prorata_fields(plan_price: float, start_date: date) -> tuple[bool, float, int, int]:
    """
    Retorna (is_prorata, billing_amount, prorated_days, days_in_month).
    Pro-rata se aplica apenas quando o contrato começa após o dia 1 do mês.
    """
    if start_date.day == 1:
        return False, plan_price, 0, 0
    dim = monthrange(start_date.year, start_date.month)[1]
    remaining = dim - start_date.day + 1
    prorated = float(
        (Decimal(str(plan_price)) * Decimal(remaining) / Decimal(dim)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
    )
    return True, prorated, remaining, dim


def _first_cycle_charge_items(
    db: Session,
    client_id: int,
    contract_id: int,
    reference_month: date,
) -> list[dict]:
    """
    Retorna ChargeItems de parcela única (installment_count=1) que:
    - Pertencem explicitamente ao contrato (itens genéricos ficam avulsos)
    - Têm start_date dentro do mês de referência (serviços do início do contrato)
    - Ainda não foram faturados (active=True e billing_count=0)

    Esses itens serão embutidos na cobrança combinada da primeira mensalidade,
    em vez de gerar faturas separadas.
    """
    month_start = reference_month.replace(day=1)
    if reference_month.month == 12:
        month_end = date(reference_month.year + 1, 1, 1)
    else:
        month_end = date(reference_month.year, reference_month.month + 1, 1)

    q = db.query(ClientChargeItem).filter(
        ClientChargeItem.is_deleted.is_(False),
        ClientChargeItem.active.is_(True),
        ClientChargeItem.client_id == client_id,
        ClientChargeItem.contract_id == contract_id,
        ClientChargeItem.installment_count == 1,
        ClientChargeItem.start_date >= month_start,
        ClientChargeItem.start_date < month_end,
    )
    result = []
    for item in q.all():
        billing_count = charge_item_effective_billing_count(db, item.id)
        if billing_count > 0:
            continue  # já faturado por outro caminho

        result.append({
            'item_id': item.id,
            'title': item.title,
            'amount': float(_quantize_amount(item.total_amount)),
        })

    return result


def _validate_contract_relationships(
    contract: Contract,
    client: Client | None,
    plan: Plan | None,
    vehicle: Vehicle | None,
    tracker: Tracker | None,
    interveniente: Client | None,
) -> None:
    """Falha fechada para contrato operacional com referência removida.

    A validação é local ao fechamento. Referências históricas de eventos de
    desinstalação não passam por ela e continuam podendo apontar para contrato
    ou veículo removido.
    """
    if not client or client.is_deleted:
        raise ValueError(
            f'Contrato #{contract.id} referencia cliente removido #{contract.client_id}.'
        )
    if not plan or plan.is_deleted:
        raise ValueError(
            f'Contrato #{contract.id} referencia plano removido #{contract.plan_id}.'
        )
    if contract.vehicle_id and (not vehicle or vehicle.is_deleted):
        raise ValueError(
            f'Contrato #{contract.id} referencia veículo removido #{contract.vehicle_id}.'
        )
    if contract.tracker_id and (not tracker or tracker.is_deleted):
        raise ValueError(
            f'Contrato #{contract.id} referencia rastreador removido #{contract.tracker_id}.'
        )
    if contract.interveniente_client_id and (
        not interveniente or interveniente.is_deleted
    ):
        raise ValueError(
            f'Responsável financeiro #{contract.interveniente_client_id} do contrato '
            f'#{contract.id} não está disponível.'
        )


def _locked_contracts(db: Session, contract_ids: set[int]) -> dict[int, Contract]:
    """Trava, em ordem estável, somente contratos usados nesta geração."""
    if not contract_ids:
        return {}
    rows = db.scalars(
        select(Contract)
        .where(Contract.id.in_(contract_ids))
        .order_by(Contract.id.asc())
        .with_for_update()
        # O contrato pode ter sido carregado pela simulação antes de a
        # exclusão concorrente comitar. Atualize o objeto do identity map com a
        # versão que o SELECT FOR UPDATE acabou de serializar.
        .execution_options(populate_existing=True)
    ).all()
    return {contract.id: contract for contract in rows}


def _validate_locked_contract_for_closure(db: Session, contract: Contract) -> None:
    client = db.get(Client, contract.client_id)
    plan = db.get(Plan, contract.plan_id)
    vehicle = db.get(Vehicle, contract.vehicle_id) if contract.vehicle_id else None
    tracker = db.get(Tracker, contract.tracker_id) if contract.tracker_id else None
    interveniente = (
        db.get(Client, contract.interveniente_client_id)
        if contract.interveniente_client_id else None
    )
    _validate_contract_relationships(
        contract, client, plan, vehicle, tracker, interveniente,
    )
