"""
Testes de integração para /api/v1/reports.

Cobertos:
- GET /revenue           → relatório de receita mensal
- GET /delinquents       → relatório de inadimplentes
- GET /client-statement  → extrato individual (por client_id)
- GET /summary           → resumo executivo (contratos por plano, receita 6 meses)
- Autorização → OPERATIONAL pode ver, CLIENT → 403
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from app.models.billing import Billing
from app.models.enums import BillingStatus

PREFIX = "/api/v1/reports"


def _make_paid_billing(db, contrato, amount=150.0):
    b = Billing(
        contract_id=contrato.id,
        client_id=contrato.client_id,
        amount=Decimal(str(amount)),
        due_date=date.today(),
        payment_date=date.today(),
        status=BillingStatus.PAID,
        paid_amount=Decimal(str(amount)),
        billing_type="recorrente",
        period_label=date.today().strftime("%m/%Y"),
        title="Pago",
    )
    db.add(b)
    db.commit()
    return b


class TestRevenueReport:
    def test_returns_list(self, http):
        r = http.get(PREFIX + "/revenue")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_returns_correct_structure(self, http, db, contrato):
        _make_paid_billing(db, contrato)
        r = http.get(PREFIX + "/revenue")
        assert r.status_code == 200
        if r.json():
            item = r.json()[0]
            assert "label" in item
            assert "total_received" in item
            assert "total_billed" in item

    def test_operational_can_access(self, http_op):
        r = http_op.get(PREFIX + "/revenue")
        assert r.status_code == 200

    def test_client_role_cannot_access(self, http_cliente):
        r = http_cliente.get(PREFIX + "/revenue")
        assert r.status_code == 403

    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.get(PREFIX + "/revenue")
        assert r.status_code == 401

    def test_filter_by_period_annual(self, http):
        r = http.get(PREFIX + "/revenue", params={"period": "annual"})
        assert r.status_code == 200

    def test_filter_by_period_quarterly(self, http):
        r = http.get(PREFIX + "/revenue", params={"period": "quarterly"})
        assert r.status_code == 200


class TestDelinquentsReport:
    def test_returns_list(self, http):
        r = http.get(PREFIX + "/delinquents")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_includes_delinquent_client(self, http, db, contrato):
        b = Billing(
            contract_id=contrato.id,
            client_id=contrato.client_id,
            amount=Decimal("200.00"),
            due_date=date(2020, 1, 1),
            status=BillingStatus.OVERDUE,
            billing_type="recorrente",
            period_label="01/2020",
            title="Vencida",
        )
        db.add(b)
        db.commit()
        r = http.get(PREFIX + "/delinquents")
        assert r.status_code == 200
        assert any(x["client_id"] == contrato.client_id for x in r.json())

    def test_structure(self, http, db, contrato):
        b = Billing(
            contract_id=contrato.id,
            client_id=contrato.client_id,
            amount=Decimal("200.00"),
            due_date=date(2020, 1, 1),
            status=BillingStatus.OVERDUE,
            billing_type="recorrente",
            period_label="01/2020",
            title="Vencida",
        )
        db.add(b)
        db.commit()
        r = http.get(PREFIX + "/delinquents")
        if r.json():
            item = r.json()[0]
            assert "client_id" in item
            assert "client_name" in item
            assert "total_open" in item
            assert "overdue_count" in item


class TestClientStatement:
    def test_returns_statement_for_existing_client(self, http, db, contrato):
        _make_paid_billing(db, contrato)
        r = http.get(f"{PREFIX}/client-statement/{contrato.client_id}")
        assert r.status_code == 200

    def test_nonexistent_client_returns_404(self, http):
        r = http.get(f"{PREFIX}/client-statement/99999")
        assert r.status_code == 404

    def test_operational_can_access(self, http_op, db, contrato):
        r = http_op.get(f"{PREFIX}/client-statement/{contrato.client_id}")
        assert r.status_code == 200


class TestExecutiveSummary:
    def test_returns_200(self, http):
        r = http.get(PREFIX + "/summary")
        assert r.status_code == 200

    def test_response_has_structure(self, http):
        r = http.get(PREFIX + "/summary")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)

    def test_contracts_by_plan_included(self, http, contrato):
        r = http.get(PREFIX + "/summary")
        assert r.status_code == 200
        data = r.json()
        assert "contracts_by_plan" in data

    def test_financial_can_access(self, http_fin):
        r = http_fin.get(PREFIX + "/summary")
        assert r.status_code == 200

    def test_client_role_cannot_access(self, http_cliente):
        r = http_cliente.get(PREFIX + "/summary")
        assert r.status_code == 403
