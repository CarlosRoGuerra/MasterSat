from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app.models.billing import Billing
from app.models.enums import BillingStatus


PREFIX = "/api/v1/ailos"


def test_manual_retry_can_take_over_a_processing_batch_reservation(
    http, db, cliente,
):
    cliente.zip_code = "28970-000"
    cliente.address_line = "Rua Principal"
    cliente.address_number = "100"
    cliente.neighborhood = "Centro"
    cliente.city = "Araruama"
    cliente.state = "RJ"
    billings = [
        Billing(
            client_id=cliente.id,
            amount=Decimal("99.90"),
            due_date=date(2099, 9, day),
            status=BillingStatus.PENDING,
            billing_type="carne",
            title=f"Parcela {day}",
        )
        for day in (10, 11)
    ]
    db.add_all(billings)
    db.commit()
    billing_ids = [billing.id for billing in billings]

    batch_response = SimpleNamespace(json={"ticketLote": "TICKET-RESERVA"})
    with patch(
        "app.services.ailos_boletos.ailos_client.request",
        return_value=batch_response,
    ):
        created = http.post(
            f"{PREFIX}/carne/lote",
            json={"billing_ids": billing_ids},
        )

    assert created.status_code == 200
    lote_id = created.json()["id"]

    single_response = SimpleNamespace(json={
        "documento": {
            "numeroDocumento": billing_ids[0],
            "nossoNumero": "12345678",
            "identificadorUnicoTitulo": "ID-12345678",
        },
        "codigoBarras": {
            "codigoBarras": "CODIGO-BARRAS",
            "linhaDigitavel": "LINHA-DIGITAVEL",
        },
        "indicadorSituacaoBoleto": "REGISTRADO",
        "valorBoleto": {"valorNominal": 99.9},
        "vencimento": {"dataVencimento": "2099-09-10"},
    })
    with patch(
        "app.services.ailos_boletos.ailos_client.request",
        return_value=single_response,
    ) as external_request:
        retried = http.post(
            f"{PREFIX}/lotes/{lote_id}/parcelas/{billing_ids[0]}/registrar",
        )

    assert retried.status_code == 200
    assert retried.json()["linha_digitavel"] == "LINHA-DIGITAVEL"
    assert retried.json()["lote_id"] == lote_id
    external_request.assert_called_once()
