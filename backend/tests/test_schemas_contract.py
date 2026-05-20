"""
Testes de validação Pydantic para schemas de Contract.

Cobertos:
- Limites de billing_day (ge=1, le=28)
- Limites de billing_cycles (ge=1, le=60)
- Defaults esperados
- Campos opcionais / parciais no ContractUpdate
- Tentativas de sabotagem
"""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.contract import ContractCreate, ContractUpdate


# ---------------------------------------------------------------------------
# ContractCreate — campo billing_day
# ---------------------------------------------------------------------------

class TestContractCreateBillingDay:
    def test_billing_day_1_accepted(self):
        c = ContractCreate(
            client_id=1, plan_id=1,
            start_date=date(2025, 1, 15),
            billing_day=1,
        )
        assert c.billing_day == 1

    def test_billing_day_28_accepted(self):
        c = ContractCreate(
            client_id=1, plan_id=1,
            start_date=date(2025, 1, 15),
            billing_day=28,
        )
        assert c.billing_day == 28

    def test_billing_day_0_rejected(self):
        with pytest.raises(ValidationError):
            ContractCreate(
                client_id=1, plan_id=1,
                start_date=date(2025, 1, 15),
                billing_day=0,
            )

    def test_billing_day_29_rejected(self):
        with pytest.raises(ValidationError):
            ContractCreate(
                client_id=1, plan_id=1,
                start_date=date(2025, 1, 15),
                billing_day=29,
            )

    def test_billing_day_negative_rejected(self):
        with pytest.raises(ValidationError):
            ContractCreate(
                client_id=1, plan_id=1,
                start_date=date(2025, 1, 15),
                billing_day=-5,
            )

    def test_billing_day_none_accepted(self):
        c = ContractCreate(
            client_id=1, plan_id=1,
            start_date=date(2025, 1, 15),
            billing_day=None,
        )
        assert c.billing_day is None


# ---------------------------------------------------------------------------
# ContractCreate — campo billing_cycles
# ---------------------------------------------------------------------------

class TestContractCreateBillingCycles:
    def test_default_cycles_is_12(self):
        c = ContractCreate(client_id=1, plan_id=1, start_date=date(2025, 1, 1))
        assert c.billing_cycles == 12

    def test_cycles_1_accepted(self):
        c = ContractCreate(client_id=1, plan_id=1, start_date=date(2025, 1, 1), billing_cycles=1)
        assert c.billing_cycles == 1

    def test_cycles_60_accepted(self):
        c = ContractCreate(client_id=1, plan_id=1, start_date=date(2025, 1, 1), billing_cycles=60)
        assert c.billing_cycles == 60

    def test_cycles_0_rejected(self):
        with pytest.raises(ValidationError):
            ContractCreate(client_id=1, plan_id=1, start_date=date(2025, 1, 1), billing_cycles=0)

    def test_cycles_61_rejected(self):
        with pytest.raises(ValidationError):
            ContractCreate(client_id=1, plan_id=1, start_date=date(2025, 1, 1), billing_cycles=61)

    def test_cycles_negative_rejected(self):
        with pytest.raises(ValidationError):
            ContractCreate(client_id=1, plan_id=1, start_date=date(2025, 1, 1), billing_cycles=-1)


# ---------------------------------------------------------------------------
# ContractCreate — defaults
# ---------------------------------------------------------------------------

class TestContractCreateDefaults:
    def test_auto_generate_billings_default_true(self):
        c = ContractCreate(client_id=1, plan_id=1, start_date=date(2025, 1, 1))
        assert c.auto_generate_billings is True

    def test_status_default_ativo(self):
        c = ContractCreate(client_id=1, plan_id=1, start_date=date(2025, 1, 1))
        assert c.status == "ativo"

    def test_vehicle_id_optional(self):
        c = ContractCreate(client_id=1, plan_id=1, start_date=date(2025, 1, 1))
        assert c.vehicle_id is None

    def test_tracker_id_optional(self):
        c = ContractCreate(client_id=1, plan_id=1, start_date=date(2025, 1, 1))
        assert c.tracker_id is None

    def test_end_date_optional(self):
        c = ContractCreate(client_id=1, plan_id=1, start_date=date(2025, 1, 1))
        assert c.end_date is None

    def test_notes_optional(self):
        c = ContractCreate(client_id=1, plan_id=1, start_date=date(2025, 1, 1))
        assert c.notes is None


# ---------------------------------------------------------------------------
# ContractCreate — XSS / injection in free-text fields
# ---------------------------------------------------------------------------

class TestContractCreateSabotage:
    def test_xss_in_notes(self):
        payload = "<script>alert(1)</script>"
        c = ContractCreate(
            client_id=1, plan_id=1,
            start_date=date(2025, 1, 1),
            notes=payload,
        )
        assert c.notes == payload  # stored as-is; escaping is the frontend's job

    def test_sql_injection_in_notes(self):
        payload = "'; DROP TABLE contracts; --"
        c = ContractCreate(
            client_id=1, plan_id=1,
            start_date=date(2025, 1, 1),
            notes=payload,
        )
        assert c.notes == payload

    def test_unicode_in_notes(self):
        c = ContractCreate(
            client_id=1, plan_id=1,
            start_date=date(2025, 1, 1),
            notes="Contrato válido — ñoño",
        )
        assert "ñoño" in c.notes

    def test_oversized_notes_accepted_by_pydantic(self):
        # Pydantic has no max_length here; the DB will enforce it
        long_notes = "A" * 10_000
        c = ContractCreate(client_id=1, plan_id=1, start_date=date(2025, 1, 1), notes=long_notes)
        assert len(c.notes) == 10_000

    def test_negative_client_id_accepted_by_schema(self):
        # Pydantic schema does NOT validate FK existence; the API layer does
        c = ContractCreate(client_id=-1, plan_id=1, start_date=date(2025, 1, 1))
        assert c.client_id == -1

    def test_zero_plan_id_accepted_by_schema(self):
        c = ContractCreate(client_id=1, plan_id=0, start_date=date(2025, 1, 1))
        assert c.plan_id == 0


# ---------------------------------------------------------------------------
# ContractUpdate — partial update schema
# ---------------------------------------------------------------------------

class TestContractUpdate:
    def test_empty_update_valid(self):
        u = ContractUpdate()
        assert u.client_id is None
        assert u.plan_id is None
        assert u.status is None

    def test_billing_day_update_valid(self):
        u = ContractUpdate(billing_day=10)
        assert u.billing_day == 10

    def test_billing_day_update_out_of_range(self):
        with pytest.raises(ValidationError):
            ContractUpdate(billing_day=29)

    def test_status_update(self):
        u = ContractUpdate(status="cancelado")
        assert u.status == "cancelado"

    def test_end_date_update(self):
        u = ContractUpdate(end_date=date(2026, 12, 31))
        assert u.end_date == date(2026, 12, 31)
