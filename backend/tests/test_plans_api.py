"""
Testes de integração para /api/v1/plans.

Cobertos:
- GET /     → listar, filtro active, busca
- POST /    → criar mensal/trimestral/semestral/anual, campos obrigatórios
- GET /{id} → sucesso, 404
- PUT /{id} → atualizar price/name/active, 404
- DELETE /  → soft-delete, 404, com contrato ativo impede deleção
- Autorização → FINANCIAL pode criar, OPERATIONAL só lê
"""
from __future__ import annotations
from decimal import Decimal
import pytest
from app.models.contract import Contract
from app.models.plan import Plan

PREFIX = "/api/v1/plans"


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestListPlans:
    def test_empty_list(self, http):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_existing_plan(self, http, plan):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert any(x["id"] == plan.id for x in r.json())

    def test_filter_active_only(self, http, db):
        p_active = Plan(name="Ativo", price=Decimal("50.00"), active=True, billing_interval_months=1)
        p_inactive = Plan(name="Inativo", price=Decimal("50.00"), active=False, billing_interval_months=1)
        db.add_all([p_active, p_inactive])
        db.commit()
        r = http.get(PREFIX + "/", params={"active_only": True})
        assert r.status_code == 200
        assert all(x["active"] for x in r.json())

    def test_excludes_soft_deleted(self, http, db, plan):
        plan.is_deleted = True
        db.commit()
        r = http.get(PREFIX + "/")
        assert all(x["id"] != plan.id for x in r.json())

    def test_operational_can_list(self, http_op, plan):
        r = http_op.get(PREFIX + "/")
        assert r.status_code == 200

    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.get(PREFIX + "/")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------

class TestCreatePlan:
    def test_create_monthly_plan(self, http):
        r = http.post(PREFIX + "/", json={
            "name": "Mensal Básico",
            "price": 89.90,
            "active": True,
            "billing_interval_months": 1,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["billing_interval_months"] == 1
        assert data["active"] is True

    def test_create_quarterly_plan(self, http):
        r = http.post(PREFIX + "/", json={
            "name": "Trimestral",
            "price": 250.00,
            "active": True,
            "billing_interval_months": 3,
        })
        assert r.status_code == 200
        assert r.json()["billing_interval_months"] == 3

    def test_create_annual_plan(self, http):
        r = http.post(PREFIX + "/", json={
            "name": "Anual",
            "price": 900.00,
            "active": True,
            "billing_interval_months": 12,
        })
        assert r.status_code == 200

    def test_missing_name_returns_422(self, http):
        r = http.post(PREFIX + "/", json={"price": 99.90, "billing_interval_months": 1})
        assert r.status_code == 422

    def test_missing_price_returns_422(self, http):
        r = http.post(PREFIX + "/", json={"name": "X", "billing_interval_months": 1})
        assert r.status_code == 422

    def test_financial_can_create(self, http_fin):
        r = http_fin.post(PREFIX + "/", json={
            "name": "Plano Financeiro", "price": 99.00, "billing_interval_months": 1,
        })
        assert r.status_code == 200

    def test_operational_cannot_create(self, http_op):
        r = http_op.post(PREFIX + "/", json={
            "name": "Plano Op", "price": 99.00, "billing_interval_months": 1,
        })
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /{id}
# ---------------------------------------------------------------------------

class TestGetPlan:
    def test_get_existing(self, http, plan):
        r = http.get(f"{PREFIX}/{plan.id}")
        assert r.status_code == 200
        assert r.json()["id"] == plan.id

    def test_get_nonexistent_returns_404(self, http):
        r = http.get(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_get_includes_active_contracts_count(self, http, plan, contrato):
        r = http.get(f"{PREFIX}/{plan.id}")
        assert r.status_code == 200
        assert r.json()["active_contracts"] >= 1


# ---------------------------------------------------------------------------
# PUT /{id}
# ---------------------------------------------------------------------------

class TestUpdatePlan:
    def test_update_price(self, http, plan):
        r = http.put(f"{PREFIX}/{plan.id}", json={"price": 149.90})
        assert r.status_code == 200
        assert abs(r.json()["price"] - 149.90) < 0.01

    def test_update_name(self, http, plan):
        r = http.put(f"{PREFIX}/{plan.id}", json={"name": "Plano Atualizado"})
        assert r.status_code == 200
        assert r.json()["name"] == "Plano Atualizado"

    def test_deactivate_plan(self, http, plan):
        r = http.put(f"{PREFIX}/{plan.id}", json={"active": False})
        assert r.status_code == 200
        assert r.json()["active"] is False

    def test_update_nonexistent_returns_404(self, http):
        r = http.put(f"{PREFIX}/99999", json={"name": "X"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /{id}
# ---------------------------------------------------------------------------

class TestDeletePlan:
    def test_soft_delete(self, http, db, plan):
        r = http.delete(f"{PREFIX}/{plan.id}")
        assert r.status_code == 200
        db.refresh(plan)
        assert plan.is_deleted is True

    def test_delete_nonexistent_returns_404(self, http):
        r = http.delete(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_delete_com_contrato_ativo_diz_quantos_e_o_que_fazer(self, http, db, plan, cliente):
        """Antes só dizia "possui contratos ativos" — o operador ficava sem saída."""
        from datetime import date

        from app.models.contract import Contract

        db.add(Contract(client_id=cliente.id, plan_id=plan.id,
                        start_date=date(2026, 1, 1), status='ativo'))
        db.commit()

        r = http.delete(f"{PREFIX}/{plan.id}")
        assert r.status_code == 400
        detalhe = r.json()['detail']
        assert plan.name in detalhe          # qual plano
        assert '1 contrato' in detalhe       # quantos travam
        assert 'desative' in detalhe.lower()  # o que fazer
        db.refresh(plan)
        assert plan.is_deleted is False

    def test_operational_cannot_delete(self, http_op, plan):
        r = http_op.delete(f"{PREFIX}/{plan.id}")
        assert r.status_code == 403
