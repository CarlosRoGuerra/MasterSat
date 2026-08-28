"""Regression tests for serialized financial billing mutations."""

from decimal import Decimal

from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.enums import BillingStatus


PREFIX = "/api/v1/billings"


def _register_ailos_boleto(db, billing_id: int) -> AilosBoleto:
    boleto = AilosBoleto(
        billing_id=billing_id,
        numero_convenio="102004",
        nosso_numero="000000301",
        linha_digitavel="08591.02006 40045.470206 00000.003012 5 14890000009990",
        codigo_barras="08595148900000099901020040045470200000000301",
    )
    db.add(boleto)
    db.commit()
    return boleto


def test_unify_rejects_duplicate_ids_after_normalization(http, db, billing_pendente):
    response = http.post(
        f"{PREFIX}/unificar",
        json={
            "billing_ids": [billing_pendente.id, billing_pendente.id],
            "due_date": "2099-01-15",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Informe pelo menos duas cobranças diferentes."
    db.refresh(billing_pendente)
    assert billing_pendente.status == BillingStatus.PENDING


def test_batch_maintenance_rejects_a_registered_ailos_boleto(
    http, db, billing_pendente,
):
    _register_ailos_boleto(db, billing_pendente.id)
    original_amount = billing_pendente.amount
    original_due_date = billing_pendente.due_date

    response = http.post(
        f"{PREFIX}/lote/manutencao",
        json={
            "billing_ids": [billing_pendente.id],
            "amount": 123.45,
            "due_date": "2099-05-10",
            "justification": "negociação",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "boleto_ailos_registrado"
    assert response.json()["detail"]["billing_ids"] == [billing_pendente.id]
    db.refresh(billing_pendente)
    assert billing_pendente.amount == original_amount
    assert billing_pendente.due_date == original_due_date


def test_unify_rejects_a_registered_ailos_boleto_without_partial_changes(
    http, db, billing_pendente, billing_vencida,
):
    _register_ailos_boleto(db, billing_vencida.id)

    response = http.post(
        f"{PREFIX}/unificar",
        json={
            "billing_ids": [billing_pendente.id, billing_vencida.id],
            "due_date": "2099-01-15",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "boleto_ailos_registrado"
    assert response.json()["detail"]["billing_ids"] == [billing_vencida.id]
    db.refresh(billing_pendente)
    db.refresh(billing_vencida)
    assert billing_pendente.status == BillingStatus.PENDING
    assert billing_vencida.status == BillingStatus.OVERDUE


def test_repeating_unify_does_not_create_a_second_effective_billing(
    http, db, billing_pendente, billing_vencida,
):
    payload = {
        "billing_ids": [billing_pendente.id, billing_vencida.id],
        "due_date": "2099-01-15",
    }

    first = http.post(f"{PREFIX}/unificar", json=payload)
    second = http.post(f"{PREFIX}/unificar", json=payload)

    assert first.status_code == 200
    assert second.status_code == 400
    assert db.query(Billing).filter(
        Billing.billing_type == "avulsa",
        Billing.is_deleted.is_(False),
    ).count() == 1


def test_unit_maintenance_rejects_a_registered_ailos_boleto(
    http, db, billing_pendente,
):
    _register_ailos_boleto(db, billing_pendente.id)

    response = http.put(
        f"{PREFIX}/{billing_pendente.id}",
        json={"amount": 123.45, "justification": "negociação"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "boleto_ailos_registrado"
    db.refresh(billing_pendente)
    assert billing_pendente.amount == Decimal("99.90")
