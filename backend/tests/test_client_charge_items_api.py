"""
Testes de integração para /api/v1/client-charge-items.

Cobertos:
- GET /     → listar, filtro client_id
- POST /    → criar item com geração automática de billings, campos obrigatórios
- PUT /{id} → atualizar, 404
- DELETE /  → soft-delete, 404
- Autorização → OPERATIONAL não cria, FINANCIAL pode
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from app.models.billing import Billing

PREFIX = "/api/v1/client-charge-items"


def _payload(client_id: int, contract_id: int | None = None) -> dict:
    return {
        "client_id": client_id,
        "contract_id": contract_id,
        "title": "Cobrança de teste",
        "description": "Desc",
        "quantity": 1,
        "unit_price": 150.0,
        "installment_count": 1,
        "start_date": "2025-06-01",
        "active": True,
        "remove_after_payment": False,
        "auto_generate_billings": False,
    }


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestListChargeItems:
    def test_empty_list(self, http):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert r.json() == []

    def test_filter_by_client_id(self, http, cliente, contrato):
        http.post(PREFIX + "/", json=_payload(cliente.id, contrato.id))
        r = http.get(PREFIX + "/", params={"client_id": cliente.id})
        assert r.status_code == 200
        assert all(x["client_id"] == cliente.id for x in r.json())

    def test_operational_can_list(self, http_op):
        r = http_op.get(PREFIX + "/")
        assert r.status_code == 200

    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.get(PREFIX + "/")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------

class TestCreateChargeItem:
    def test_create_without_auto_billing(self, http, db, cliente, contrato):
        r = http.post(PREFIX + "/", json=_payload(cliente.id, contrato.id))
        assert r.status_code == 200
        data = r.json()
        assert data["client_id"] == cliente.id
        assert abs(data["total_amount"] - 150.0) < 0.01
        # Sem auto_generate_billings → sem billings criados
        billings = db.query(Billing).filter(Billing.item_id == data["id"]).count()
        assert billings == 0

    def test_create_with_auto_billing(self, http, db, cliente, contrato):
        payload = {**_payload(cliente.id, contrato.id), "auto_generate_billings": True}
        r = http.post(PREFIX + "/", json=payload)
        assert r.status_code == 200
        item_id = r.json()["id"]
        billings = db.query(Billing).filter(Billing.item_id == item_id).count()
        assert billings >= 1

    def test_create_installments(self, http, db, cliente, contrato):
        payload = {
            **_payload(cliente.id, contrato.id),
            "installment_count": 3,
            "unit_price": 300.0,
            "auto_generate_billings": True,
        }
        r = http.post(PREFIX + "/", json=payload)
        assert r.status_code == 200
        item_id = r.json()["id"]
        billings = db.query(Billing).filter(Billing.item_id == item_id).all()
        assert len(billings) == 3
        for i, b in enumerate(sorted(billings, key=lambda x: x.installment_number)):
            assert b.installment_number == i + 1
            assert b.installment_total == 3

    def test_missing_client_id_returns_422(self, http):
        r = http.post(PREFIX + "/", json={"title": "X", "unit_price": 100.0, "quantity": 1})
        assert r.status_code == 422

    def test_financial_can_create(self, http_fin, cliente, contrato):
        r = http_fin.post(PREFIX + "/", json=_payload(cliente.id, contrato.id))
        assert r.status_code == 200

    def test_operational_cannot_create(self, http_op, cliente, contrato):
        r = http_op.post(PREFIX + "/", json=_payload(cliente.id, contrato.id))
        assert r.status_code == 403

    def test_rejects_contract_from_another_client(self, http, outro_cliente, contrato):
        r = http.post(PREFIX + "/", json=_payload(outro_cliente.id, contrato.id))
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# PUT /{id}
# ---------------------------------------------------------------------------

class TestUpdateChargeItem:
    def _create(self, http, cliente, contrato):
        r = http.post(PREFIX + "/", json=_payload(cliente.id, contrato.id))
        return r.json()["id"]

    def test_update_title(self, http, cliente, contrato):
        item_id = self._create(http, cliente, contrato)
        r = http.put(f"{PREFIX}/{item_id}", json={"title": "Título Atualizado"})
        assert r.status_code == 200
        assert r.json()["title"] == "Título Atualizado"

    def test_update_nonexistent_returns_404(self, http):
        r = http.put(f"{PREFIX}/99999", json={"title": "X"})
        assert r.status_code == 404

    def test_cannot_force_server_managed_status(self, http, cliente, contrato):
        item_id = self._create(http, cliente, contrato)
        r = http.put(f"{PREFIX}/{item_id}", json={"status": "concluido", "active": False})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /{id}
# ---------------------------------------------------------------------------

class TestDeleteChargeItem:
    def _create(self, http, cliente, contrato):
        r = http.post(PREFIX + "/", json=_payload(cliente.id, contrato.id))
        return r.json()["id"]

    def test_soft_delete(self, http, db, cliente, contrato):
        from app.models.client_charge_item import ClientChargeItem
        item_id = self._create(http, cliente, contrato)
        r = http.delete(f"{PREFIX}/{item_id}")
        assert r.status_code == 200
        obj = db.get(ClientChargeItem, item_id)
        assert obj.is_deleted is True

    def test_delete_nonexistent_returns_404(self, http):
        r = http.delete(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_operational_cannot_delete(self, http_op, cliente, contrato):
        item_id = self._create(http, cliente, contrato)
        r = http_op.delete(f"{PREFIX}/{item_id}")
        assert r.status_code == 403
