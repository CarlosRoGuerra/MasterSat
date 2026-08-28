from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.billing import Billing
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus


CONTRACTS_PREFIX = "/api/v1/contracts"
BILLING_CLOSURE_PREFIX = "/api/v1/billing-closure"


def test_contract_with_pending_charge_item_cannot_be_soft_deleted(http, db, contrato):
    pending_item = ClientChargeItem(
        client_id=contrato.client_id,
        contract_id=contrato.id,
        vehicle_id=contrato.vehicle_id,
        tracker_id=contrato.tracker_id,
        title="Instalação pendente",
        quantity=1,
        unit_price=Decimal("150.00"),
        total_amount=Decimal("150.00"),
        installment_count=1,
        start_date=date(2025, 5, 1),
        active=True,
        status="ativo",
    )
    db.add(pending_item)
    db.commit()

    response = http.delete(f"{CONTRACTS_PREFIX}/{contrato.id}")

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            f"Contrato possui lançamento ativo ainda não faturado "
            f"(#{pending_item.id}). Fature ou remova o lançamento antes de excluir o contrato."
        )
    }
    db.refresh(contrato)
    assert contrato.is_deleted is False
    assert contrato.status == "ativo"


def test_active_legacy_charge_already_fully_billed_does_not_block_contract_delete(
    http, db, contrato,
):
    billed_item = ClientChargeItem(
        client_id=contrato.client_id,
        contract_id=contrato.id,
        title="Serviço já faturado",
        quantity=1,
        unit_price=Decimal("50.00"),
        total_amount=Decimal("50.00"),
        installment_count=1,
        start_date=date(2025, 5, 1),
        active=True,
        status="ativo",
    )
    db.add(billed_item)
    db.flush()
    db.add(Billing(
        contract_id=contrato.id,
        client_id=contrato.client_id,
        item_id=billed_item.id,
        title=billed_item.title,
        billing_type="item",
        installment_number=1,
        installment_total=1,
        amount=Decimal("50.00"),
        due_date=date(2025, 5, 15),
        status=BillingStatus.PENDING,
        period_label="05/2025",
    ))
    db.commit()

    response = http.delete(f"{CONTRACTS_PREFIX}/{contrato.id}")

    assert response.status_code == 200
    db.refresh(contrato)
    assert contrato.is_deleted is True


def test_partially_billed_active_charge_still_blocks_contract_delete(
    http, db, contrato,
):
    partial_item = ClientChargeItem(
        client_id=contrato.client_id,
        contract_id=contrato.id,
        title="Serviço parcialmente faturado",
        quantity=1,
        unit_price=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        installment_count=2,
        start_date=date(2025, 5, 1),
        active=True,
        status="ativo",
    )
    db.add(partial_item)
    db.flush()
    db.add(Billing(
        contract_id=contrato.id,
        client_id=contrato.client_id,
        item_id=partial_item.id,
        title=partial_item.title,
        billing_type="item",
        installment_number=1,
        installment_total=2,
        amount=Decimal("50.00"),
        due_date=date(2025, 5, 15),
        status=BillingStatus.PENDING,
        period_label="05/2025",
    ))
    db.commit()

    response = http.delete(f"{CONTRACTS_PREFIX}/{contrato.id}")

    assert response.status_code == 409
    db.refresh(contrato)
    assert contrato.is_deleted is False


def test_simulation_rejects_active_contract_with_deleted_vehicle(
    http, db, contrato, veiculo,
):
    veiculo.is_deleted = True
    db.commit()

    response = http.get(
        f"{BILLING_CLOSURE_PREFIX}/simulate",
        params={"reference_month": "2025-05"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": f"Contrato #{contrato.id} referencia veículo removido #{veiculo.id}."
    }


def test_simulation_rejects_active_contract_with_deleted_tracker(
    http, db, contrato, rastreador_instalado,
):
    rastreador_instalado.is_deleted = True
    db.commit()

    response = http.get(
        f"{BILLING_CLOSURE_PREFIX}/simulate",
        params={"reference_month": "2025-05"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            f"Contrato #{contrato.id} referencia rastreador removido "
            f"#{rastreador_instalado.id}."
        )
    }


def test_simulation_rejects_active_contract_with_deleted_payer(
    http, db, contrato, outro_cliente,
):
    contrato.interveniente_client_id = outro_cliente.id
    outro_cliente.is_deleted = True
    db.commit()

    response = http.get(
        f"{BILLING_CLOSURE_PREFIX}/simulate",
        params={"reference_month": "2025-05"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            f"Responsável financeiro #{outro_cliente.id} do contrato "
            f"#{contrato.id} não está disponível."
        )
    }


def test_simulation_rejects_active_contract_with_deleted_plan(
    http, db, contrato, plan,
):
    plan.is_deleted = True
    db.commit()

    response = http.get(
        f"{BILLING_CLOSURE_PREFIX}/simulate",
        params={"reference_month": "2025-05"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": f"Contrato #{contrato.id} referencia plano removido #{plan.id}."
    }


def test_simulation_rejects_active_contract_with_deleted_client(
    http, db, contrato, cliente,
):
    cliente.is_deleted = True
    db.commit()

    response = http.get(
        f"{BILLING_CLOSURE_PREFIX}/simulate",
        params={"reference_month": "2025-05"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": f"Contrato #{contrato.id} referencia cliente removido #{cliente.id}."
    }


def test_deleted_contract_is_ignored_by_recurring_closure(http, db, contrato):
    contrato.is_deleted = True
    contrato.status = "cancelado"
    db.commit()

    response = http.get(
        f"{BILLING_CLOSURE_PREFIX}/simulate",
        params={"reference_month": "2025-05"},
    )

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["to_generate"] == 0


def test_uninstall_snapshot_survives_deleted_contract_and_keeps_historical_due_day(
    http, db, contrato, uninstall_event,
):
    uninstall_event.payer_client_id = contrato.client_id
    contrato.is_deleted = True
    contrato.status = "cancelado"
    db.commit()

    response = http.post(
        f"{BILLING_CLOSURE_PREFIX}/generate",
        params={"reference_month": "2025-05"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["generated"] == 0
    assert payload["uninstall_events_processed"] == 1
    db.refresh(uninstall_event)
    billing = db.get(Billing, uninstall_event.billing_id)
    assert billing is not None
    assert billing.due_date == date(2025, 5, contrato.billing_day)


def test_simulation_rejects_legacy_pending_charge_linked_to_deleted_contract(
    http, db, contrato,
):
    legacy_item = ClientChargeItem(
        client_id=contrato.client_id,
        contract_id=contrato.id,
        title="Serviço legado pendente",
        quantity=1,
        unit_price=Decimal("50.00"),
        total_amount=Decimal("50.00"),
        installment_count=1,
        start_date=date(2025, 5, 1),
        active=True,
        status="ativo",
    )
    db.add(legacy_item)
    db.flush()
    contrato.is_deleted = True
    contrato.status = "cancelado"
    db.commit()

    response = http.get(
        f"{BILLING_CLOSURE_PREFIX}/simulate",
        params={"reference_month": "2025-05"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            f"Lançamento #{legacy_item.id} está ativo, mas referencia contrato "
            f"removido #{contrato.id}. Reconcilie o lançamento antes do fechamento."
        )
    }


def test_simulation_ignores_fully_billed_legacy_item_of_deleted_contract(
    http, db, contrato,
):
    legacy_item = ClientChargeItem(
        client_id=contrato.client_id,
        contract_id=contrato.id,
        title="Serviço legado faturado",
        quantity=1,
        unit_price=Decimal("50.00"),
        total_amount=Decimal("50.00"),
        installment_count=1,
        start_date=date(2025, 5, 1),
        active=True,
        status="ativo",
    )
    db.add(legacy_item)
    db.flush()
    db.add(Billing(
        contract_id=contrato.id,
        client_id=contrato.client_id,
        item_id=legacy_item.id,
        title=legacy_item.title,
        billing_type="item",
        installment_number=1,
        installment_total=1,
        amount=Decimal("50.00"),
        due_date=date(2025, 5, 15),
        status=BillingStatus.PENDING,
        period_label="05/2025",
    ))
    contrato.is_deleted = True
    contrato.status = "cancelado"
    db.commit()

    response = http.get(
        f"{BILLING_CLOSURE_PREFIX}/simulate",
        params={"reference_month": "2025-05"},
    )

    assert response.status_code == 200
    assert response.json()["charge_items"] == []


def test_inconsistent_legacy_item_aborts_generation_before_any_billing(
    http, db, cliente, plan, contrato,
):
    valid_contract = Contract(
        client_id=cliente.id,
        plan_id=plan.id,
        start_date=date(2025, 5, 1),
        status="ativo",
        billing_day=15,
    )
    db.add(valid_contract)
    db.flush()
    legacy_item = ClientChargeItem(
        client_id=contrato.client_id,
        contract_id=contrato.id,
        title="Serviço legado pendente",
        quantity=1,
        unit_price=Decimal("50.00"),
        total_amount=Decimal("50.00"),
        installment_count=1,
        start_date=date(2025, 5, 1),
        active=True,
        status="ativo",
    )
    db.add(legacy_item)
    contrato.is_deleted = True
    contrato.status = "cancelado"
    db.commit()
    billings_before = db.query(Billing).count()

    response = http.post(
        f"{BILLING_CLOSURE_PREFIX}/generate",
        params={"reference_month": "2025-05"},
    )

    assert response.status_code == 409
    assert db.query(Billing).count() == billings_before
