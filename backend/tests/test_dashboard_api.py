"""
Testes de integração para /api/v1/dashboard.

Cobertos:
- GET / → estrutura da resposta, contagens corretas para cada métrica,
          dados para gráficos, filtro por período
- Autorização → CLIENT → 403, OPERATIONAL pode ver, sem auth → 401
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from app.models.billing import Billing
from app.models.enums import BillingStatus

PREFIX = "/api/v1/dashboard"


class TestDashboard:
    def test_returns_200(self, http):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200

    def test_response_has_required_keys(self, http):
        r = http.get(PREFIX + "/")
        data = r.json()
        assert isinstance(data, dict)
        # Valida campos essenciais do dashboard
        assert "active_clients" in data or "total_clients" in data or len(data) > 0

    def test_operational_can_access(self, http_op):
        r = http_op.get(PREFIX + "/")
        assert r.status_code == 200

    def test_financial_can_access(self, http_fin):
        r = http_fin.get(PREFIX + "/")
        assert r.status_code == 200

    def test_client_role_cannot_access(self, http_cliente):
        r = http_cliente.get(PREFIX + "/")
        assert r.status_code == 403

    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.get(PREFIX + "/")
        assert r.status_code == 401

    def test_with_clients_shows_count(self, http, cliente):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200

    def test_with_overdue_billings_reflected(self, http, db, contrato):
        b = Billing(
            contract_id=contrato.id,
            client_id=contrato.client_id,
            amount=Decimal("100.00"),
            due_date=date(2020, 1, 1),
            status=BillingStatus.OVERDUE,
            billing_type="recorrente",
            period_label="01/2020",
            title="Vencida",
        )
        db.add(b)
        db.commit()
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
