"""
Testes de integração para /api/v1/delinquency.

Cobertos:
- POST /refresh → executa verificação, marca inadimplentes, restaura ativos
- GET  /status  → retorna contagem de clientes e cobranças vencidas
- Autorização   → OPERATIONAL pode ver status, CLIENT não pode
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus, ClientStatus

PREFIX = "/api/v1/delinquency"


def _make_overdue_billing(db, cliente):
    b = Billing(
        client_id=cliente.id,
        amount=Decimal("100.00"),
        due_date=date(2020, 1, 1),
        status=BillingStatus.OVERDUE,
        billing_type="recorrente",
        period_label="01/2020",
        title="Vencida",
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


class TestDelinquencyRefresh:
    def test_marks_client_delinquent(self, http, db, cliente):
        _make_overdue_billing(db, cliente)
        r = http.post(PREFIX + "/refresh")
        assert r.status_code == 200
        db.refresh(cliente)
        assert cliente.status == ClientStatus.DELINQUENT

    def test_restores_active_when_no_overdue(self, http, db, cliente):
        # Coloca em inadimplente manualmente
        cliente.status = ClientStatus.DELINQUENT
        db.commit()
        # Sem billing vencida → deve voltar para ativo
        r = http.post(PREFIX + "/refresh")
        assert r.status_code == 200
        db.refresh(cliente)
        assert cliente.status == ClientStatus.ACTIVE

    def test_returns_summary(self, http, db, cliente):
        _make_overdue_billing(db, cliente)
        r = http.post(PREFIX + "/refresh")
        assert r.status_code == 200
        data = r.json()
        assert "marcados_inadimplentes" in data
        assert "restaurados_ativos" in data

    def test_active_client_with_paid_billing_stays_active(self, http, db, cliente):
        b = Billing(
            client_id=cliente.id,
            amount=Decimal("100.00"),
            due_date=date(2020, 1, 1),
            status=BillingStatus.PAID,
            billing_type="recorrente",
            period_label="01/2020",
            title="Paga",
        )
        db.add(b)
        db.commit()
        r = http.post(PREFIX + "/refresh")
        assert r.status_code == 200
        db.refresh(cliente)
        assert cliente.status == ClientStatus.ACTIVE

    def test_financial_can_refresh(self, http_fin, db, cliente):
        r = http_fin.post(PREFIX + "/refresh")
        assert r.status_code == 200

    def test_operational_cannot_refresh(self, http_op):
        r = http_op.post(PREFIX + "/refresh")
        assert r.status_code == 403

    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.post(PREFIX + "/refresh")
        assert r.status_code == 401


class TestDelinquencyStatus:
    def test_status_returns_structure(self, http):
        r = http.get(PREFIX + "/status")
        assert r.status_code == 200
        data = r.json()
        assert "clientes_inadimplentes" in data
        assert "cobrancas_vencidas" in data
        assert "valor_total_vencido" in data

    def test_status_counts_delinquent_clients(self, http, db, cliente):
        _make_overdue_billing(db, cliente)
        http.post(PREFIX + "/refresh")
        r = http.get(PREFIX + "/status")
        assert r.status_code == 200
        assert r.json()["clientes_inadimplentes"] >= 1

    def test_status_value_total(self, http, db, cliente):
        _make_overdue_billing(db, cliente)
        http.post(PREFIX + "/refresh")
        r = http.get(PREFIX + "/status")
        assert r.status_code == 200
        assert r.json()["valor_total_vencido"] >= 100.0

    def test_excludes_deleted_billings(self, http, db, cliente):
        b = _make_overdue_billing(db, cliente)
        b.is_deleted = True
        db.commit()
        r = http.get(PREFIX + "/status")
        assert r.status_code == 200
        assert r.json()["cobrancas_vencidas"] == 0

    def test_operational_can_see_status(self, http_op):
        r = http_op.get(PREFIX + "/status")
        assert r.status_code == 200

    def test_client_role_cannot_see_status(self, http_cliente):
        r = http_cliente.get(PREFIX + "/status")
        assert r.status_code == 403
