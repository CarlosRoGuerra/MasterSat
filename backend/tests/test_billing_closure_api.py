"""
Testes de integração para /api/v1/billing-closure.

Cobertos:
- GET /simulate          → estrutura da resposta, uninstall_events presentes,
                           filtros pf/pj/client, mês ausente → 422,
                           client_id ausente quando filter_type=client → 422
- GET /simulate/pdf      → content-type application/pdf, bytes não vazios, header %PDF
- POST /generate         → execução síncrona, retorna status='completed' imediatamente
                           com campos generated/total_amount/grand_total,
                           mês ausente → 422, client_id ausente → 422
- Autorização:
    OPERATIONAL → 403 em todos os endpoints
    FINANCIAL   → 200 em todos os endpoints
    sem auth    → 401
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from app.models.uninstall_event import UninstallEvent

PREFIX = "/api/v1/billing-closure"
REF_MONTH = "2025-05"


# ---------------------------------------------------------------------------
# GET /simulate
# ---------------------------------------------------------------------------

class TestSimulate:
    def test_requires_reference_month(self, http):
        r = http.get(PREFIX + "/simulate")
        assert r.status_code == 422

    def test_returns_200_with_month(self, http):
        r = http.get(PREFIX + "/simulate", params={"reference_month": REF_MONTH})
        assert r.status_code == 200

    def test_response_structure(self, http):
        r = http.get(PREFIX + "/simulate", params={"reference_month": REF_MONTH})
        data = r.json()
        for key in (
            "reference_month", "total_contracts", "to_generate", "already_generated",
            "items", "uninstall_events", "total_uninstall_fees", "grand_total",
        ):
            assert key in data

    def test_reference_month_value_in_response(self, http):
        r = http.get(PREFIX + "/simulate", params={"reference_month": REF_MONTH})
        assert r.json()["reference_month"] == "05/2025"

    def test_invalid_month_format_returns_422(self, http):
        r = http.get(PREFIX + "/simulate", params={"reference_month": "05-2025"})
        assert r.status_code == 422

    def test_invalid_month_value_returns_422(self, http):
        r = http.get(PREFIX + "/simulate", params={"reference_month": "2025-13"})
        assert r.status_code == 422

    def test_filter_client_without_client_id_returns_422(self, http):
        r = http.get(PREFIX + "/simulate", params={
            "reference_month": REF_MONTH,
            "filter_type": "client",
        })
        assert r.status_code == 422

    def test_filter_pf_returns_200(self, http):
        r = http.get(PREFIX + "/simulate", params={
            "reference_month": REF_MONTH,
            "filter_type": "pf",
        })
        assert r.status_code == 200

    def test_filter_pj_returns_200(self, http):
        r = http.get(PREFIX + "/simulate", params={
            "reference_month": REF_MONTH,
            "filter_type": "pj",
        })
        assert r.status_code == 200

    def test_uninstall_event_appears_in_response(self, http, db, cliente, veiculo):
        e = UninstallEvent(
            vehicle_id=veiculo.id,
            client_id=cliente.id,
            uninstall_date=date(2025, 5, 10),
            fee_amount=Decimal("100.00"),
            status="pending",
        )
        db.add(e)
        db.commit()
        r = http.get(PREFIX + "/simulate", params={"reference_month": REF_MONTH})
        assert r.status_code == 200
        data = r.json()
        assert len(data["uninstall_events"]) >= 1
        item = data["uninstall_events"][0]
        assert "event_id" in item
        assert "fee_amount" in item
        assert "skipped" in item
        assert item["client_id"] == cliente.id

    def test_skipped_event_appears_with_reason(self, http, db, cliente, veiculo):
        e = UninstallEvent(
            vehicle_id=veiculo.id,
            client_id=cliente.id,
            uninstall_date=date(2025, 5, 10),
            fee_amount=Decimal("1.00"),
            status="pending",
        )
        db.add(e)
        db.commit()
        r = http.get(PREFIX + "/simulate", params={"reference_month": REF_MONTH})
        assert r.status_code == 200
        skipped = [e for e in r.json()["uninstall_events"] if e["skipped"]]
        assert len(skipped) >= 1
        assert skipped[0]["skip_reason"] is not None

    def test_operational_cannot_access(self, http_op):
        r = http_op.get(PREFIX + "/simulate", params={"reference_month": REF_MONTH})
        assert r.status_code == 403

    def test_financial_can_access(self, http_fin):
        r = http_fin.get(PREFIX + "/simulate", params={"reference_month": REF_MONTH})
        assert r.status_code == 200

    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.get(PREFIX + "/simulate", params={"reference_month": REF_MONTH})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /simulate/pdf
# ---------------------------------------------------------------------------

class TestSimulatePdf:
    def test_returns_200(self, http):
        r = http.get(PREFIX + "/simulate/pdf", params={"reference_month": REF_MONTH})
        assert r.status_code == 200

    def test_content_type_is_pdf(self, http):
        r = http.get(PREFIX + "/simulate/pdf", params={"reference_month": REF_MONTH})
        assert "application/pdf" in r.headers.get("content-type", "")

    def test_pdf_has_content(self, http):
        r = http.get(PREFIX + "/simulate/pdf", params={"reference_month": REF_MONTH})
        assert len(r.content) > 0

    def test_pdf_starts_with_pdf_header(self, http):
        r = http.get(PREFIX + "/simulate/pdf", params={"reference_month": REF_MONTH})
        assert r.content[:4] == b"%PDF"

    def test_requires_reference_month(self, http):
        r = http.get(PREFIX + "/simulate/pdf")
        assert r.status_code == 422

    def test_operational_cannot_access(self, http_op):
        r = http_op.get(PREFIX + "/simulate/pdf", params={"reference_month": REF_MONTH})
        assert r.status_code == 403

    def test_financial_can_access(self, http_fin):
        r = http_fin.get(PREFIX + "/simulate/pdf", params={"reference_month": REF_MONTH})
        assert r.status_code == 200

    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.get(PREFIX + "/simulate/pdf", params={"reference_month": REF_MONTH})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /generate
# ---------------------------------------------------------------------------

class TestGenerate:
    def test_returns_200(self, http):
        r = http.post(PREFIX + "/generate", params={"reference_month": REF_MONTH})
        assert r.status_code == 200

    def test_status_is_completed(self, http):
        r = http.post(PREFIX + "/generate", params={"reference_month": REF_MONTH})
        assert r.json()["status"] == "completed"

    def test_response_echoes_reference_month(self, http):
        r = http.post(PREFIX + "/generate", params={"reference_month": REF_MONTH})
        assert r.json()["reference_month"] == REF_MONTH

    def test_reference_month_da_resposta_e_aceito_de_volta(self, http):
        """Round-trip: o mês devolvido tem que ser reenviável à própria API.

        O `**result` do serviço sobrescrevia o eco do parâmetro e a resposta
        saía como 05/2025 — formato que o endpoint rejeita com 422.
        """
        primeira = http.post(PREFIX + "/generate", params={"reference_month": REF_MONTH})
        devolvido = primeira.json()["reference_month"]

        segunda = http.post(PREFIX + "/generate", params={"reference_month": devolvido})
        assert segunda.status_code == 200

    def test_response_traz_mes_formatado_para_exibicao(self, http):
        r = http.post(PREFIX + "/generate", params={"reference_month": REF_MONTH})
        assert r.json()["reference_month_label"] == "05/2025"

    def test_response_has_result_fields(self, http):
        r = http.post(PREFIX + "/generate", params={"reference_month": REF_MONTH})
        data = r.json()
        for key in (
            "status", "reference_month", "generated", "total_amount",
            "uninstall_fees_generated", "uninstall_fees_skipped",
            "services_generated", "total_services_amount", "grand_total",
        ):
            assert key in data, f"missing key: {key}"

    def test_generated_is_integer(self, http):
        r = http.post(PREFIX + "/generate", params={"reference_month": REF_MONTH})
        assert isinstance(r.json()["generated"], int)

    def test_grand_total_is_numeric(self, http):
        r = http.post(PREFIX + "/generate", params={"reference_month": REF_MONTH})
        assert isinstance(r.json()["grand_total"], (int, float))

    def test_requires_reference_month(self, http):
        r = http.post(PREFIX + "/generate")
        assert r.status_code == 422

    def test_filter_client_without_client_id_returns_422(self, http):
        r = http.post(PREFIX + "/generate", params={
            "reference_month": REF_MONTH,
            "filter_type": "client",
        })
        assert r.status_code == 422

    def test_second_call_generates_nothing(self, http):
        http.post(PREFIX + "/generate", params={"reference_month": REF_MONTH})
        r2 = http.post(PREFIX + "/generate", params={"reference_month": REF_MONTH})
        assert r2.status_code == 200
        assert r2.json()["generated"] == 0

    def test_operational_cannot_generate(self, http_op):
        r = http_op.post(PREFIX + "/generate", params={"reference_month": REF_MONTH})
        assert r.status_code == 403

    def test_financial_can_generate(self, http_fin):
        r = http_fin.post(PREFIX + "/generate", params={"reference_month": REF_MONTH})
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.post(PREFIX + "/generate", params={"reference_month": REF_MONTH})
        assert r.status_code == 401
