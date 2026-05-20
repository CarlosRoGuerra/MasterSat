"""
Testes unitários para app.services.financial.

Cobertos:
- add_months: transição de mês, overflow de ano, fevereiro
- normalize_due_date: com e sem billing_day, clamp de fim-de-mês
- period_label_for_date: mensal, trimestral, semestral, anual
- refresh_overdue_statuses: pendente→vencida, vencida→pendente
- generate_monthly_billings: contagem correta, respeito ao end_date,
  deduplicação, force=True, plan não encontrado
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.billing import Billing
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.models.plan import Plan
from app.services.financial import (
    add_months,
    generate_monthly_billings,
    normalize_due_date,
    period_label_for_date,
    refresh_overdue_statuses,
)


# ---------------------------------------------------------------------------
# add_months
# ---------------------------------------------------------------------------

class TestAddMonths:
    def test_simple_addition(self):
        assert add_months(date(2025, 1, 15), 1) == date(2025, 2, 15)

    def test_year_overflow(self):
        assert add_months(date(2025, 12, 1), 1) == date(2026, 1, 1)

    def test_multiple_months(self):
        assert add_months(date(2025, 1, 1), 12) == date(2026, 1, 1)

    def test_february_28_from_31(self):
        # 31 Jan + 1 month = 28 Feb (non-leap year)
        assert add_months(date(2025, 1, 31), 1) == date(2025, 2, 28)

    def test_february_29_leap_year(self):
        # 31 Jan 2024 + 1 month = 29 Feb 2024 (leap year)
        assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)

    def test_march_from_february_28(self):
        assert add_months(date(2025, 2, 28), 1) == date(2025, 3, 28)

    def test_zero_months(self):
        d = date(2025, 6, 15)
        assert add_months(d, 0) == d

    def test_24_months(self):
        assert add_months(date(2025, 3, 10), 24) == date(2027, 3, 10)


# ---------------------------------------------------------------------------
# normalize_due_date
# ---------------------------------------------------------------------------

class TestNormalizeDueDate:
    def test_no_billing_day_uses_start_day(self):
        result = normalize_due_date(date(2025, 1, 15), cycle=1)
        assert result == date(2025, 2, 15)

    def test_billing_day_overrides_day(self):
        result = normalize_due_date(date(2025, 1, 1), cycle=1, billing_day=20)
        assert result == date(2025, 2, 20)

    def test_billing_day_clamped_to_month_end(self):
        # billing_day=31 for February → 28
        result = normalize_due_date(date(2025, 1, 31), cycle=1, billing_day=31)
        assert result == date(2025, 2, 28)

    def test_billing_day_28_in_february(self):
        result = normalize_due_date(date(2025, 1, 1), cycle=1, billing_day=28)
        assert result == date(2025, 2, 28)

    def test_cycle_0(self):
        result = normalize_due_date(date(2025, 3, 15), cycle=0, billing_day=15)
        assert result == date(2025, 3, 15)

    def test_cycle_12_one_year(self):
        result = normalize_due_date(date(2025, 1, 10), cycle=12, billing_day=10)
        assert result == date(2026, 1, 10)


# ---------------------------------------------------------------------------
# period_label_for_date
# ---------------------------------------------------------------------------

class TestPeriodLabelForDate:
    def test_monthly(self):
        assert period_label_for_date(date(2025, 6, 15), 1) == "06/2025"

    def test_monthly_leading_zero(self):
        assert period_label_for_date(date(2025, 1, 1), 1) == "01/2025"

    def test_quarterly_q1(self):
        assert period_label_for_date(date(2025, 2, 1), 3) == "2025 • T1"

    def test_quarterly_q4(self):
        assert period_label_for_date(date(2025, 12, 1), 3) == "2025 • T4"

    def test_semi_annual_s1(self):
        assert period_label_for_date(date(2025, 6, 1), 6) == "2025 • S1"

    def test_semi_annual_s2(self):
        assert period_label_for_date(date(2025, 7, 1), 6) == "2025 • S2"

    def test_annual(self):
        assert period_label_for_date(date(2025, 6, 1), 12) == "2025"


# ---------------------------------------------------------------------------
# refresh_overdue_statuses
# ---------------------------------------------------------------------------

class TestRefreshOverdueStatuses:
    def _make_billing(self, db, contract_id, client_id, due_date, status):
        b = Billing(
            contract_id=contract_id,
            client_id=client_id,
            amount=Decimal("100.00"),
            due_date=due_date,
            status=status,
            billing_type="recorrente",
            period_label=due_date.strftime("%m/%Y"),
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        return b

    def test_pending_past_due_becomes_overdue(self, db, contrato):
        b = self._make_billing(
            db, contrato.id, contrato.client_id,
            due_date=date(2020, 1, 1),
            status=BillingStatus.PENDING,
        )
        refresh_overdue_statuses(db)
        db.refresh(b)
        assert b.status == BillingStatus.OVERDUE

    def test_overdue_future_due_becomes_pending(self, db, contrato):
        b = self._make_billing(
            db, contrato.id, contrato.client_id,
            due_date=date(2099, 12, 31),
            status=BillingStatus.OVERDUE,
        )
        refresh_overdue_statuses(db)
        db.refresh(b)
        assert b.status == BillingStatus.PENDING

    def test_paid_not_changed(self, db, contrato):
        b = self._make_billing(
            db, contrato.id, contrato.client_id,
            due_date=date(2020, 1, 1),
            status=BillingStatus.PAID,
        )
        refresh_overdue_statuses(db)
        db.refresh(b)
        assert b.status == BillingStatus.PAID

    def test_canceled_not_changed(self, db, contrato):
        b = self._make_billing(
            db, contrato.id, contrato.client_id,
            due_date=date(2020, 1, 1),
            status=BillingStatus.CANCELED,
        )
        refresh_overdue_statuses(db)
        db.refresh(b)
        assert b.status == BillingStatus.CANCELED

    def test_deleted_billing_not_affected(self, db, contrato):
        b = self._make_billing(
            db, contrato.id, contrato.client_id,
            due_date=date(2020, 1, 1),
            status=BillingStatus.PENDING,
        )
        b.is_deleted = True
        db.commit()
        refresh_overdue_statuses(db)
        db.refresh(b)
        assert b.status == BillingStatus.PENDING  # not updated because is_deleted=True


# ---------------------------------------------------------------------------
# generate_monthly_billings
# ---------------------------------------------------------------------------

class TestGenerateMonthlyBillings:
    def test_generates_correct_count(self, db, contrato, plan):
        created = generate_monthly_billings(db, contrato, cycles=6)
        assert len(created) == 6

    def test_generates_12_by_default(self, db, contrato, plan):
        created = generate_monthly_billings(db, contrato, cycles=12)
        assert len(created) == 12

    def test_billing_amount_equals_plan_price(self, db, contrato, plan):
        created = generate_monthly_billings(db, contrato, cycles=1)
        assert float(created[0].amount) == pytest.approx(float(plan.price))

    def test_billing_dates_are_monthly(self, db, contrato, plan):
        # generate_monthly_billings uses range(cycles) → cycles 0,1,2
        # cycle=0 → start_date itself; cycle=1 → +1 month; etc.
        created = generate_monthly_billings(db, contrato, cycles=3)
        months = [b.due_date.month for b in created]
        start_month = contrato.start_date.month
        expected = [(start_month - 1 + i) % 12 + 1 for i in range(3)]
        assert months == expected

    def test_skips_duplicates_by_default(self, db, contrato, plan):
        first = generate_monthly_billings(db, contrato, cycles=3)
        second = generate_monthly_billings(db, contrato, cycles=3)
        assert len(second) == 0  # no new billings created

    def test_force_updates_existing(self, db, contrato, plan):
        generate_monthly_billings(db, contrato, cycles=3)
        updated = generate_monthly_billings(db, contrato, cycles=3, force=True)
        assert len(updated) == 3

    def test_respects_end_date(self, db, cliente, plan):
        c = Contract(
            client_id=cliente.id,
            plan_id=plan.id,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 3, 31),
            status="ativo",
            billing_day=1,
        )
        db.add(c)
        db.commit()
        created = generate_monthly_billings(db, c, cycles=12)
        assert len(created) == 3  # Jan, Feb, Mar only

    def test_raises_if_plan_not_found(self, db, cliente):
        c = Contract(
            client_id=cliente.id,
            plan_id=99999,  # non-existent
            start_date=date(2025, 1, 1),
            status="ativo",
        )
        db.add(c)
        db.commit()
        with pytest.raises(ValueError, match="Plano não encontrado"):
            generate_monthly_billings(db, c, cycles=1)

    def test_billing_client_id_matches_contract(self, db, contrato, plan):
        created = generate_monthly_billings(db, contrato, cycles=1)
        assert created[0].client_id == contrato.client_id

    def test_billing_contract_id_matches(self, db, contrato, plan):
        created = generate_monthly_billings(db, contrato, cycles=1)
        assert created[0].contract_id == contrato.id

    def test_billing_uses_billing_day(self, db, cliente, plan):
        c = Contract(
            client_id=cliente.id,
            plan_id=plan.id,
            start_date=date(2025, 1, 1),
            status="ativo",
            billing_day=20,
        )
        db.add(c)
        db.commit()
        created = generate_monthly_billings(db, c, cycles=3)
        for b in created:
            assert b.due_date.day == 20

    def test_past_due_billing_gets_overdue_status(self, db, cliente, plan):
        c = Contract(
            client_id=cliente.id,
            plan_id=plan.id,
            start_date=date(2020, 1, 1),
            status="ativo",
            billing_day=1,
        )
        db.add(c)
        db.commit()
        created = generate_monthly_billings(db, c, cycles=1)
        assert created[0].status == BillingStatus.OVERDUE
