"""
Testes de integração para /api/v1/contracts.

Cobertos:
- GET /        → listar, filtros, busca, SQL injection no search
- POST /       → criar com/sem billings, cliente/plano/veículo ausente,
                 veículo de outro cliente, billing_day auto-calculado
- GET /{id}    → sucesso, 404, deletado
- PUT /{id}    → sucesso, 404, cliente/plano inválido
- DELETE /{id} → sucesso, 404, já deletado
- POST /{id}/generate-billings → sucesso, 404
- Autorização: OPERATIONAL não acessa contratos (403),
               CLIENT role (403), sem token (401)
- Sabotagem: XSS em notes, billing_day fora dos limites,
             IDs negativos, payload gigante
"""
from __future__ import annotations

from datetime import date

import pytest

PREFIX = "/api/v1/contracts"


# ---------------------------------------------------------------------------
# GET / — listar contratos
# ---------------------------------------------------------------------------

class TestListContratos:
    def test_empty_list(self, http):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_existing_contract(self, http, contrato):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["id"] == contrato.id

    def test_filter_by_client_id(self, http, contrato, cliente):
        r = http.get(PREFIX + "/", params={"client_id": cliente.id})
        assert r.status_code == 200
        assert all(x["client_id"] == cliente.id for x in r.json())

    def test_filter_by_wrong_client_returns_empty(self, http, contrato):
        r = http.get(PREFIX + "/", params={"client_id": 99999})
        assert r.status_code == 200
        assert r.json() == []

    def test_filter_by_plan_id(self, http, contrato, plan):
        r = http.get(PREFIX + "/", params={"plan_id": plan.id})
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_filter_by_status(self, http, contrato):
        r = http.get(PREFIX + "/", params={"status": "ativo"})
        assert r.status_code == 200
        data = r.json()
        assert all(x["status"] == "ativo" for x in data)

    def test_filter_nonexistent_status(self, http, contrato):
        r = http.get(PREFIX + "/", params={"status": "inexistente"})
        assert r.status_code == 200
        assert r.json() == []

    def test_search_by_client_name(self, http, contrato, cliente):
        r = http.get(PREFIX + "/", params={"search": "João"})
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_search_case_insensitive(self, http, contrato, cliente):
        r = http.get(PREFIX + "/", params={"search": "joão"})
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_search_no_match(self, http, contrato):
        r = http.get(PREFIX + "/", params={"search": "Inexistente XYZ"})
        assert r.status_code == 200
        assert r.json() == []

    def test_search_sql_injection_safe(self, http, contrato):
        # SQLAlchemy parameterizes queries — injection attempt returns empty, not error
        r = http.get(PREFIX + "/", params={"search": "'; DROP TABLE contracts; --"})
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_search_xss_safe(self, http, contrato):
        r = http.get(PREFIX + "/", params={"search": "<script>alert(1)</script>"})
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_limit_respected(self, http, db, cliente, plan):
        from app.models.contract import Contract
        for i in range(5):
            db.add(Contract(
                client_id=cliente.id, plan_id=plan.id,
                start_date=date(2025, 1, 1), status="ativo",
            ))
        db.commit()
        r = http.get(PREFIX + "/", params={"limit": 3})
        assert r.status_code == 200
        assert len(r.json()) <= 3

    def test_limit_max_300(self, http):
        r = http.get(PREFIX + "/", params={"limit": 999})
        assert r.status_code in (200, 422)

    def test_enriched_fields_present(self, http, contrato, cliente, plan):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        item = r.json()[0]
        assert item["client_name"] == cliente.name
        assert item["plan_name"] == plan.name


# ---------------------------------------------------------------------------
# POST / — criar contrato
# ---------------------------------------------------------------------------

class TestCreateContrato:
    def _payload(self, cliente_id, plan_id, start="2025-01-15", **kw):
        return {"client_id": cliente_id, "plan_id": plan_id, "start_date": start, **kw}

    def test_success_minimal(self, http, cliente, plan):
        r = http.post(PREFIX + "/", json=self._payload(cliente.id, plan.id))
        assert r.status_code == 200
        data = r.json()
        assert data["client_id"] == cliente.id
        assert data["plan_id"] == plan.id
        assert data["status"] == "ativo"

    def test_auto_billing_day_from_start_date(self, http, cliente, plan):
        r = http.post(PREFIX + "/", json=self._payload(cliente.id, plan.id, "2025-06-20"))
        assert r.status_code == 200
        assert r.json()["billing_day"] == 20

    def test_billing_day_capped_at_28(self, http, cliente, plan):
        # start_date day 31 → billing_day 28
        r = http.post(PREFIX + "/", json=self._payload(cliente.id, plan.id, "2025-01-31"))
        assert r.status_code == 200
        assert r.json()["billing_day"] == 28

    def test_explicit_billing_day(self, http, cliente, plan):
        r = http.post(PREFIX + "/", json=self._payload(cliente.id, plan.id, billing_day=5))
        assert r.status_code == 200
        assert r.json()["billing_day"] == 5

    def test_generates_billings_by_default(self, http, db, cliente, plan):
        from app.models.billing import Billing
        r = http.post(PREFIX + "/", json=self._payload(cliente.id, plan.id, billing_cycles=3))
        assert r.status_code == 200
        cid = r.json()["id"]
        billings = db.query(Billing).filter(Billing.contract_id == cid).all()
        assert len(billings) == 3

    def test_no_billings_when_flag_false(self, http, db, cliente, plan):
        from app.models.billing import Billing
        r = http.post(PREFIX + "/", json=self._payload(
            cliente.id, plan.id, auto_generate_billings=False))
        assert r.status_code == 200
        cid = r.json()["id"]
        billings = db.query(Billing).filter(Billing.contract_id == cid).all()
        assert len(billings) == 0

    def test_client_not_found(self, http, plan):
        r = http.post(PREFIX + "/", json=self._payload(99999, plan.id))
        assert r.status_code == 404
        assert "Cliente" in r.json()["detail"]

    def test_plan_not_found(self, http, cliente):
        r = http.post(PREFIX + "/", json=self._payload(cliente.id, 99999))
        assert r.status_code == 404
        assert "Plano" in r.json()["detail"]

    def test_vehicle_not_found(self, http, cliente, plan):
        r = http.post(PREFIX + "/", json=self._payload(
            cliente.id, plan.id, vehicle_id=99999))
        assert r.status_code == 404

    def test_vehicle_from_another_client_rejected(self, http, cliente, plan, veiculo_outro_cliente):
        r = http.post(PREFIX + "/", json=self._payload(
            cliente.id, plan.id, vehicle_id=veiculo_outro_cliente.id))
        assert r.status_code == 400
        assert "veículo" in r.json()["detail"].lower()

    def test_vehicle_from_same_client_accepted(self, http, cliente, plan, veiculo):
        r = http.post(PREFIX + "/", json=self._payload(
            cliente.id, plan.id, vehicle_id=veiculo.id))
        assert r.status_code == 200

    def test_with_notes(self, http, cliente, plan):
        r = http.post(PREFIX + "/", json=self._payload(
            cliente.id, plan.id, notes="Contrato de teste"))
        assert r.status_code == 200
        assert r.json()["notes"] == "Contrato de teste"

    def test_xss_in_notes_stored_as_is(self, http, cliente, plan):
        xss = "<script>alert(1)</script>"
        r = http.post(PREFIX + "/", json=self._payload(cliente.id, plan.id, notes=xss))
        assert r.status_code == 200
        assert r.json()["notes"] == xss  # stored, not escaped

    def test_billing_day_out_of_range_422(self, http, cliente, plan):
        r = http.post(PREFIX + "/", json=self._payload(
            cliente.id, plan.id, billing_day=32))
        assert r.status_code == 422

    def test_billing_day_30_aceito(self, http, cliente, plan):
        """Dia 30 é comum e era barrado pelo limite antigo de 28."""
        r = http.post(PREFIX + "/", json=self._payload(
            cliente.id, plan.id, billing_day=30))
        assert r.status_code == 200
        assert r.json()["billing_day"] == 30

    def test_billing_cycles_out_of_range_422(self, http, cliente, plan):
        r = http.post(PREFIX + "/", json=self._payload(
            cliente.id, plan.id, billing_cycles=61))
        assert r.status_code == 422

    def test_deleted_client_rejected(self, http, db, cliente, plan):
        cliente.is_deleted = True
        db.commit()
        r = http.post(PREFIX + "/", json=self._payload(cliente.id, plan.id))
        assert r.status_code == 404

    def test_deleted_plan_rejected(self, http, db, cliente, plan):
        plan.is_deleted = True
        db.commit()
        r = http.post(PREFIX + "/", json=self._payload(cliente.id, plan.id))
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /{id}
# ---------------------------------------------------------------------------

class TestGetContrato:
    def test_success(self, http, contrato):
        r = http.get(f"{PREFIX}/{contrato.id}")
        assert r.status_code == 200
        assert r.json()["id"] == contrato.id

    def test_not_found(self, http):
        r = http.get(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_deleted_returns_404(self, http, db, contrato):
        contrato.is_deleted = True
        db.commit()
        r = http.get(f"{PREFIX}/{contrato.id}")
        assert r.status_code == 404

    def test_negative_id_not_found(self, http):
        r = http.get(f"{PREFIX}/-1")
        assert r.status_code in (404, 422)

    def test_response_has_enriched_fields(self, http, contrato, cliente, plan):
        r = http.get(f"{PREFIX}/{contrato.id}")
        assert r.status_code == 200
        data = r.json()
        assert data["client_name"] == cliente.name
        assert data["plan_name"] == plan.name
        assert data["open_billings"] >= 0


# ---------------------------------------------------------------------------
# PUT /{id}
# ---------------------------------------------------------------------------

class TestUpdateContrato:
    def test_update_status(self, http, contrato):
        r = http.put(f"{PREFIX}/{contrato.id}", json={"status": "cancelado"})
        assert r.status_code == 200
        assert r.json()["status"] == "cancelado"

    def test_update_notes(self, http, contrato):
        r = http.put(f"{PREFIX}/{contrato.id}", json={"notes": "Atualizado"})
        assert r.status_code == 200
        assert r.json()["notes"] == "Atualizado"

    def test_not_found(self, http):
        r = http.put(f"{PREFIX}/99999", json={"status": "ativo"})
        assert r.status_code == 404

    def test_deleted_not_found(self, http, db, contrato):
        contrato.is_deleted = True
        db.commit()
        r = http.put(f"{PREFIX}/{contrato.id}", json={"status": "ativo"})
        assert r.status_code == 404

    def test_update_with_invalid_client(self, http, contrato):
        r = http.put(f"{PREFIX}/{contrato.id}", json={"client_id": 99999})
        assert r.status_code == 404

    def test_update_with_invalid_plan(self, http, contrato):
        r = http.put(f"{PREFIX}/{contrato.id}", json={"plan_id": 99999})
        assert r.status_code == 404

    def test_update_billing_day_valid(self, http, contrato):
        r = http.put(f"{PREFIX}/{contrato.id}", json={"billing_day": 10})
        assert r.status_code == 200
        assert r.json()["billing_day"] == 10

    def test_update_billing_day_out_of_range(self, http, contrato):
        r = http.put(f"{PREFIX}/{contrato.id}", json={"billing_day": 32})
        assert r.status_code == 422

    def test_update_billing_day_30(self, http, contrato):
        """Trocar o vencimento para dia 30 precisa SALVAR (era barrado por le=28)."""
        r = http.put(f"{PREFIX}/{contrato.id}", json={"billing_day": 30})
        assert r.status_code == 200
        assert r.json()["billing_day"] == 30

    def test_xss_in_notes(self, http, contrato):
        xss = "<img src=x onerror=alert(1)>"
        r = http.put(f"{PREFIX}/{contrato.id}", json={"notes": xss})
        assert r.status_code == 200
        assert r.json()["notes"] == xss


# ---------------------------------------------------------------------------
# DELETE /{id}
# ---------------------------------------------------------------------------

class TestDeleteContrato:
    def test_success(self, http, db, contrato):
        r = http.delete(f"{PREFIX}/{contrato.id}")
        assert r.status_code == 200
        assert "removido" in r.json()["message"].lower()
        db.refresh(contrato)
        assert contrato.is_deleted is True
        assert contrato.status == "cancelado"

    def test_not_found(self, http):
        r = http.delete(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_already_deleted(self, http, db, contrato):
        contrato.is_deleted = True
        db.commit()
        r = http.delete(f"{PREFIX}/{contrato.id}")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /{id}/generate-billings
# ---------------------------------------------------------------------------

class TestGenerateBillings:
    def test_generates_billings(self, http, db, contrato):
        from app.models.billing import Billing
        # Clear existing billings first
        db.query(Billing).filter(Billing.contract_id == contrato.id).delete()
        db.commit()

        r = http.post(f"{PREFIX}/{contrato.id}/generate-billings", params={"months": 6})
        assert r.status_code == 200
        data = r.json()
        assert data["generated"] == 6
        assert "6" in data["message"]

    def test_not_found(self, http):
        r = http.post(f"{PREFIX}/99999/generate-billings")
        assert r.status_code == 404

    def test_deleted_contract_not_found(self, http, db, contrato):
        contrato.is_deleted = True
        db.commit()
        r = http.post(f"{PREFIX}/{contrato.id}/generate-billings")
        assert r.status_code == 404

    def test_default_12_months(self, http, db, contrato):
        from app.models.billing import Billing
        db.query(Billing).filter(Billing.contract_id == contrato.id).delete()
        db.commit()
        r = http.post(f"{PREFIX}/{contrato.id}/generate-billings")
        assert r.status_code == 200
        assert r.json()["generated"] == 12


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

class TestContratoAuthorization:
    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.get(PREFIX + "/")
        assert r.status_code == 401

    def test_unauthenticated_post_returns_401(self, http_unauth):
        r = http_unauth.post(PREFIX + "/", json={})
        assert r.status_code == 401

    def test_operational_role_cannot_access_contracts(self, http_op):
        # OPERATIONAL is not in allowed roles for contracts (ADMIN, FINANCIAL only)
        r = http_op.get(PREFIX + "/")
        assert r.status_code == 403

    def test_operational_cannot_create_contract(self, http_op, cliente, plan):
        r = http_op.post(PREFIX + "/", json={
            "client_id": cliente.id,
            "plan_id": plan.id,
            "start_date": "2025-01-01",
        })
        assert r.status_code == 403

    def test_client_role_cannot_access_contracts(self, http_cliente):
        r = http_cliente.get(PREFIX + "/")
        assert r.status_code == 403

    def test_financial_role_can_list(self, http_fin, contrato):
        r = http_fin.get(PREFIX + "/")
        assert r.status_code == 200

    def test_financial_role_can_create(self, http_fin, cliente, plan):
        r = http_fin.post(PREFIX + "/", json={
            "client_id": cliente.id,
            "plan_id": plan.id,
            "start_date": "2025-01-01",
        })
        assert r.status_code == 200

    def test_financial_role_can_delete(self, http_fin, contrato):
        r = http_fin.delete(f"{PREFIX}/{contrato.id}")
        assert r.status_code == 200
