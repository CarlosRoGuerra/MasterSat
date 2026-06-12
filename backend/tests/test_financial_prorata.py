"""
Testes unitários para as funções de pro-rata e delinquência em financial.py.

Cobertos:
- prorated_amount: cálculo exato, borda de início, borda de fim, full period
- generate_prorated_first_billing: criação de billings, valor correto por dias,
  taxa de instalação separada, título dinâmico "Mensalidade — N dias"
- current_cycle_bounds: retorna limites corretos do ciclo atual
- mark_delinquent_clients: marcação, restauração, sem alteração desnecessária
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus, ClientStatus
from app.models.contract import Contract
from app.models.plan import Plan
from app.services.financial import (
    current_cycle_bounds,
    generate_prorated_first_billing,
    mark_delinquent_clients,
    prorated_amount,
)


# ---------------------------------------------------------------------------
# prorated_amount
# ---------------------------------------------------------------------------

class TestProratedAmount:
    def test_full_period_returns_full_price(self):
        price = Decimal("300.00")
        start = date(2025, 3, 1)
        end = date(2025, 3, 31)
        result = prorated_amount(price, start, end, end)
        assert result == price

    def test_half_period_returns_half(self):
        price = Decimal("300.00")
        start = date(2025, 3, 1)
        end = date(2025, 3, 31)
        # 16 days out of 31 (Mar 1–16)
        cutoff = date(2025, 3, 16)
        result = prorated_amount(price, start, end, cutoff)
        expected = (price * 16 / 31).quantize(Decimal("0.01"))
        assert result == expected

    def test_single_day(self):
        price = Decimal("310.00")
        start = date(2025, 3, 1)
        end = date(2025, 3, 31)
        cutoff = date(2025, 3, 1)
        result = prorated_amount(price, start, end, cutoff)
        expected = (price * 1 / 31).quantize(Decimal("0.01"))
        assert result == expected

    def test_cutoff_before_start_returns_zero(self):
        price = Decimal("300.00")
        start = date(2025, 3, 15)
        end = date(2025, 3, 31)
        cutoff = date(2025, 3, 10)
        result = prorated_amount(price, start, end, cutoff)
        assert result == Decimal("0.00")

    def test_february_28_days(self):
        price = Decimal("280.00")
        start = date(2025, 2, 1)
        end = date(2025, 2, 28)
        cutoff = date(2025, 2, 14)
        result = prorated_amount(price, start, end, cutoff)
        expected = (price * 14 / 28).quantize(Decimal("0.01"))
        assert result == expected

    def test_february_leap_year_29_days(self):
        price = Decimal("290.00")
        start = date(2024, 2, 1)
        end = date(2024, 2, 29)
        cutoff = date(2024, 2, 15)
        result = prorated_amount(price, start, end, cutoff)
        expected = (price * 15 / 29).quantize(Decimal("0.01"))
        assert result == expected

    def test_result_rounded_to_cents(self):
        price = Decimal("100.00")
        start = date(2025, 3, 1)
        end = date(2025, 3, 31)
        cutoff = date(2025, 3, 10)
        result = prorated_amount(price, start, end, cutoff)
        assert result == result.quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# generate_prorated_first_billing
# ---------------------------------------------------------------------------

class TestGenerateProratedFirstBilling:
    def test_creates_prorata_billing(self, db, cliente, plan):
        contract = Contract(
            client_id=cliente.id,
            plan_id=plan.id,
            start_date=date(2025, 3, 15),
            status="ativo",
            billing_day=15,
        )
        db.add(contract)
        db.commit()
        created = generate_prorated_first_billing(db, contract, plan, date(2025, 3, 15))
        assert len(created) >= 1
        prorata = next(b for b in created if b.billing_type == "prorata")
        assert prorata is not None

    def test_prorata_title_has_days(self, db, cliente, plan):
        contract = Contract(
            client_id=cliente.id,
            plan_id=plan.id,
            start_date=date(2025, 3, 20),
            status="ativo",
            billing_day=20,
        )
        db.add(contract)
        db.commit()
        created = generate_prorated_first_billing(db, contract, plan, date(2025, 3, 20))
        prorata = next(b for b in created if b.billing_type == "prorata")
        # Title should contain number of days (31-20+1=12 days in March)
        assert "12" in prorata.title

    def test_prorata_amount_is_less_than_full(self, db, cliente, plan):
        contract = Contract(
            client_id=cliente.id,
            plan_id=plan.id,
            start_date=date(2025, 3, 15),
            status="ativo",
            billing_day=15,
        )
        db.add(contract)
        db.commit()
        created = generate_prorated_first_billing(db, contract, plan, date(2025, 3, 15))
        prorata = next(b for b in created if b.billing_type == "prorata")
        assert float(prorata.amount) < float(plan.price)

    def test_prorata_full_month_on_day_1(self, db, cliente, plan):
        contract = Contract(
            client_id=cliente.id,
            plan_id=plan.id,
            start_date=date(2025, 3, 1),
            status="ativo",
            billing_day=1,
        )
        db.add(contract)
        db.commit()
        created = generate_prorated_first_billing(db, contract, plan, date(2025, 3, 1))
        prorata = next(b for b in created if b.billing_type == "prorata")
        assert float(prorata.amount) == pytest.approx(float(plan.price), rel=1e-2)

    def test_creates_installation_fee_billing(self, db, cliente, plan):
        contract = Contract(
            client_id=cliente.id,
            plan_id=plan.id,
            start_date=date(2025, 3, 15),
            status="ativo",
            billing_day=15,
        )
        db.add(contract)
        db.commit()
        created = generate_prorated_first_billing(db, contract, plan, date(2025, 3, 15), installation_fee=200.0)
        assert len(created) == 2
        fee = next(b for b in created if b.billing_type == "taxa_instalacao")
        assert float(fee.amount) == pytest.approx(200.0)

    def test_no_installation_fee_creates_only_prorata(self, db, cliente, plan):
        contract = Contract(
            client_id=cliente.id,
            plan_id=plan.id,
            start_date=date(2025, 3, 15),
            status="ativo",
            billing_day=15,
        )
        db.add(contract)
        db.commit()
        created = generate_prorated_first_billing(db, contract, plan, date(2025, 3, 15), installation_fee=0.0)
        assert len(created) == 1

    def test_past_due_date_creates_overdue_status(self, db, cliente, plan):
        contract = Contract(
            client_id=cliente.id,
            plan_id=plan.id,
            start_date=date(2020, 1, 1),
            status="ativo",
            billing_day=1,
        )
        db.add(contract)
        db.commit()
        created = generate_prorated_first_billing(db, contract, plan, date(2020, 1, 1))
        prorata = next(b for b in created if b.billing_type == "prorata")
        assert prorata.status == BillingStatus.OVERDUE


# ---------------------------------------------------------------------------
# current_cycle_bounds
# ---------------------------------------------------------------------------

class TestCurrentCycleBounds:
    def test_first_month(self, plan):
        contract = Contract(
            client_id=1, plan_id=plan.id,
            start_date=date(2025, 1, 15), status="ativo",
        )
        start, end = current_cycle_bounds(contract, plan, date(2025, 1, 20))
        assert start == date(2025, 1, 15)
        assert end == date(2025, 2, 14)

    def test_second_month(self, plan):
        contract = Contract(
            client_id=1, plan_id=plan.id,
            start_date=date(2025, 1, 15), status="ativo",
        )
        start, end = current_cycle_bounds(contract, plan, date(2025, 2, 20))
        assert start == date(2025, 2, 15)
        assert end == date(2025, 3, 14)

    def test_boundary_last_day_of_cycle(self, plan):
        contract = Contract(
            client_id=1, plan_id=plan.id,
            start_date=date(2025, 1, 1), status="ativo",
        )
        start, end = current_cycle_bounds(contract, plan, date(2025, 1, 31))
        assert start == date(2025, 1, 1)
        assert end == date(2025, 1, 31)

    def test_quarterly_plan(self, db, cliente):
        plan_q = Plan(name="Trimestral", price=Decimal("300.00"), active=True, billing_interval_months=3)
        db.add(plan_q)
        db.commit()
        contract = Contract(
            client_id=cliente.id, plan_id=plan_q.id,
            start_date=date(2025, 1, 1), status="ativo",
        )
        start, end = current_cycle_bounds(contract, plan_q, date(2025, 2, 15))
        assert start == date(2025, 1, 1)
        assert end == date(2025, 3, 31)


# ---------------------------------------------------------------------------
# mark_delinquent_clients
# ---------------------------------------------------------------------------

class TestMarkDelinquentClients:
    def _make_overdue(self, db, cliente):
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

    def test_marks_active_client_with_overdue_as_delinquent(self, db, cliente):
        self._make_overdue(db, cliente)
        result = mark_delinquent_clients(db)
        db.refresh(cliente)
        assert cliente.status == ClientStatus.DELINQUENT
        assert result["marcados_inadimplentes"] >= 1

    def test_restores_delinquent_client_when_no_overdue(self, db, cliente):
        cliente.status = ClientStatus.DELINQUENT
        db.commit()
        result = mark_delinquent_clients(db)
        db.refresh(cliente)
        assert cliente.status == ClientStatus.ACTIVE
        assert result["restaurados_ativos"] >= 1

    def test_already_delinquent_with_overdue_stays_delinquent(self, db, cliente):
        cliente.status = ClientStatus.DELINQUENT
        db.commit()
        self._make_overdue(db, cliente)
        result = mark_delinquent_clients(db)
        db.refresh(cliente)
        assert cliente.status == ClientStatus.DELINQUENT
        assert result["marcados_inadimplentes"] == 0  # already was delinquent

    def test_paid_billing_does_not_cause_delinquency(self, db, cliente):
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
        mark_delinquent_clients(db)
        db.refresh(cliente)
        assert cliente.status == ClientStatus.ACTIVE

    def test_deleted_billing_does_not_cause_delinquency(self, db, cliente):
        b = Billing(
            client_id=cliente.id,
            amount=Decimal("100.00"),
            due_date=date(2020, 1, 1),
            status=BillingStatus.OVERDUE,
            billing_type="recorrente",
            period_label="01/2020",
            title="Deletada",
            is_deleted=True,
        )
        db.add(b)
        db.commit()
        mark_delinquent_clients(db)
        db.refresh(cliente)
        assert cliente.status == ClientStatus.ACTIVE

    def test_returns_summary_dict(self, db, cliente):
        result = mark_delinquent_clients(db)
        assert "marcados_inadimplentes" in result
        assert "restaurados_ativos" in result
        assert "total_inadimplentes_agora" in result
