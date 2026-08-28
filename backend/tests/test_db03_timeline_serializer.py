from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader

from app.models.contract import Contract


def _pdf_text(content: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)


def test_timeline_identifies_removed_historical_plan_and_vehicle(
    http, db, cliente, contrato, plan, veiculo,
):
    contrato.status = "cancelado"
    plan.is_deleted = True
    veiculo.is_deleted = True
    db.commit()

    response = http.get(f"/api/v1/clients/{cliente.id}/timeline-pdf")

    assert response.status_code == 200
    text = _pdf_text(response.content)
    assert f"Plano: {plan.name} (removido)" in text
    assert f"Veículo: {veiculo.plate} (removido)" in text


def test_timeline_keeps_canceled_contract_but_omits_soft_deleted_contract(
    http, db, cliente, contrato, plan,
):
    contrato.status = "cancelado"
    removed = Contract(
        client_id=cliente.id,
        plan_id=plan.id,
        start_date=contrato.start_date,
        status="cancelado",
        is_deleted=True,
    )
    db.add(removed)
    db.commit()

    response = http.get(f"/api/v1/clients/{cliente.id}/timeline-pdf")

    assert response.status_code == 200
    text = _pdf_text(response.content)
    assert f"Contrato #{contrato.id}" in text
    assert f"Contrato #{removed.id}" not in text


def test_link_vehicle_rejects_soft_deleted_vehicle(http, db, rastreador, veiculo):
    veiculo.is_deleted = True
    db.commit()

    response = http.post(
        f"/api/v1/trackers/{rastreador.id}/link-vehicle",
        json={"vehicle_id": veiculo.id},
    )

    assert response.status_code == 404


def test_link_vehicle_rejects_vehicle_whose_client_was_deleted(
    http, db, rastreador, veiculo, cliente,
):
    cliente.is_deleted = True
    db.commit()

    response = http.post(
        f"/api/v1/trackers/{rastreador.id}/link-vehicle",
        json={"vehicle_id": veiculo.id},
    )

    assert response.status_code == 409


def test_link_vehicle_rejects_soft_deleted_intervening_payer(
    http, db, rastreador, veiculo, plan, outro_cliente,
):
    outro_cliente.is_deleted = True
    db.commit()

    response = http.post(
        f"/api/v1/trackers/{rastreador.id}/link-vehicle",
        json={
            "vehicle_id": veiculo.id,
            "plan_id": plan.id,
            "interveniente_client_id": outro_cliente.id,
        },
    )

    assert response.status_code == 404
