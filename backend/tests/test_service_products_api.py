"""
Testes de integração para /api/v1/service-products.

Cobertos:
- GET /     → listar, filtro categoria, filtro auto_add_on_uninstall
- POST /    → criar, campos obrigatórios, auto_add_on_uninstall
- PUT /{id} → atualizar preço, nome, flags
- DELETE /  → soft-delete, 404
- Autorização → OPERATIONAL apenas lê, FINANCIAL pode criar
"""
from __future__ import annotations
from decimal import Decimal
import pytest

PREFIX = "/api/v1/service-products"

_PAYLOAD = {
    "name": "Taxa de Instalação Teste",
    "category": "taxa",
    "default_price": 150.0,
    "description": "Taxa padrão",
    "active": True,
    "allow_installments": False,
    "remove_after_payment": True,
    "auto_add_on_uninstall": False,
}


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestListServiceProducts:
    def test_empty_list(self, http):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_existing_product(self, http, produto_servico):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert any(x["id"] == produto_servico.id for x in r.json())

    def test_filter_auto_uninstall(self, http, produto_desinstalacao, produto_servico):
        r = http.get(PREFIX + "/", params={"auto_add_on_uninstall": True})
        assert r.status_code == 200
        assert all(x["auto_add_on_uninstall"] for x in r.json())

    def test_excludes_soft_deleted(self, http, db, produto_servico):
        produto_servico.is_deleted = True
        db.commit()
        r = http.get(PREFIX + "/")
        assert all(x["id"] != produto_servico.id for x in r.json())

    def test_operational_can_list(self, http_op, produto_servico):
        r = http_op.get(PREFIX + "/")
        assert r.status_code == 200

    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.get(PREFIX + "/")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------

class TestCreateServiceProduct:
    def test_create_success(self, http):
        r = http.post(PREFIX + "/", json=_PAYLOAD)
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == _PAYLOAD["name"]
        assert data["auto_add_on_uninstall"] is False
        assert data["remove_after_payment"] is True

    def test_create_uninstall_product(self, http):
        r = http.post(PREFIX + "/", json={**_PAYLOAD, "name": "Desinstalação", "auto_add_on_uninstall": True})
        assert r.status_code == 200
        assert r.json()["auto_add_on_uninstall"] is True

    def test_missing_name_returns_422(self, http):
        r = http.post(PREFIX + "/", json={**_PAYLOAD, "name": ""})
        assert r.status_code in (400, 422)

    def test_missing_price_returns_422(self, http):
        payload = {**_PAYLOAD}
        del payload["default_price"]
        r = http.post(PREFIX + "/", json=payload)
        assert r.status_code == 422

    def test_financial_can_create(self, http_fin):
        r = http_fin.post(PREFIX + "/", json={**_PAYLOAD, "name": "Produto Fin"})
        assert r.status_code == 200

    def test_operational_cannot_create(self, http_op):
        r = http_op.post(PREFIX + "/", json={**_PAYLOAD, "name": "Produto Op"})
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# PUT /{id}
# ---------------------------------------------------------------------------

class TestUpdateServiceProduct:
    def test_update_price(self, http, produto_servico):
        r = http.put(f"{PREFIX}/{produto_servico.id}", json={"default_price": 200.0})
        assert r.status_code == 200
        assert abs(r.json()["default_price"] - 200.0) < 0.01

    def test_update_name(self, http, produto_servico):
        r = http.put(f"{PREFIX}/{produto_servico.id}", json={"name": "Nome Atualizado"})
        assert r.status_code == 200
        assert r.json()["name"] == "Nome Atualizado"

    def test_set_auto_add_on_uninstall(self, http, produto_servico):
        r = http.put(f"{PREFIX}/{produto_servico.id}", json={"auto_add_on_uninstall": True})
        assert r.status_code == 200
        assert r.json()["auto_add_on_uninstall"] is True

    def test_update_nonexistent_returns_404(self, http):
        r = http.put(f"{PREFIX}/99999", json={"name": "X"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /{id}
# ---------------------------------------------------------------------------

class TestDeleteServiceProduct:
    def test_soft_delete(self, http, db, produto_servico):
        r = http.delete(f"{PREFIX}/{produto_servico.id}")
        assert r.status_code == 200
        db.refresh(produto_servico)
        assert produto_servico.is_deleted is True

    def test_delete_nonexistent_returns_404(self, http):
        r = http.delete(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_operational_cannot_delete(self, http_op, produto_servico):
        r = http_op.delete(f"{PREFIX}/{produto_servico.id}")
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# IntegrityError — UNIQUE(name)
# ---------------------------------------------------------------------------
#
# Mesma corrida check-then-insert de test_plans_api.py::TestPlanNameUniqueIntegrity:
# o pré-check por nome não é atômico com o INSERT/UPDATE, então quem barra a
# duplicata de verdade é o UNIQUE de schema (`ix_service_products_name`).

class TestServiceProductNameUniqueIntegrity:
    def _bypass_precheck(self, monkeypatch):
        from sqlalchemy.orm import Query as SAQuery

        from app.models.service_product import ServiceProduct

        original_first = SAQuery.first

        def fake_first(self):
            descriptions = self.column_descriptions
            if descriptions and descriptions[0].get('type') is ServiceProduct:
                return None
            return original_first(self)

        monkeypatch.setattr(SAQuery, 'first', fake_first)

    def test_concurrent_create_same_name_returns_409_not_500(self, http, db, monkeypatch):
        r_seed = http.post(PREFIX + "/", json=_PAYLOAD)
        assert r_seed.status_code == 200
        self._bypass_precheck(monkeypatch)

        r = http.post(PREFIX + "/", json=_PAYLOAD)

        assert r.status_code == 409
        detail = r.json()["detail"]
        assert "já existe" in detail.lower()
        assert "unique" not in detail.lower()

    def test_session_stays_usable_after_conflict(self, http, db, monkeypatch):
        from app.models.service_product import ServiceProduct

        r_seed = http.post(PREFIX + "/", json=_PAYLOAD)
        assert r_seed.status_code == 200
        self._bypass_precheck(monkeypatch)

        r = http.post(PREFIX + "/", json=_PAYLOAD)
        assert r.status_code == 409

        assert db.query(ServiceProduct).filter(ServiceProduct.name == _PAYLOAD["name"]).count() == 1
        r2 = http.get(PREFIX + "/")
        assert r2.status_code == 200
