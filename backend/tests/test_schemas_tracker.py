"""
Testes de validação Pydantic para schemas de Tracker.

Cobertos:
- Normalização e validação do IMEI
- Normalização de campos de texto
- Normalização de campos numéricos (SIM/ICCID)
- Limites do billing_day em TrackerLinkPayload
- Tentativas de sabotagem: SQL injection, XSS, payloads oversized
"""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.models.enums import TrackerStatus
from app.schemas.tracker import TrackerCreate, TrackerLinkPayload, TrackerUpdate


# ---------------------------------------------------------------------------
# IMEI normalization
# ---------------------------------------------------------------------------

class TestImeiNormalization:
    def test_digits_only_passthrough(self):
        t = TrackerCreate(imei="123456789012345", brand="X", model="Y")
        assert t.imei == "123456789012345"

    def test_removes_dashes(self):
        t = TrackerCreate(imei="12-34-56-78-90-12-345", brand="X", model="Y")
        assert t.imei == "123456789012345"

    def test_removes_spaces(self):
        t = TrackerCreate(imei="  123 456 789 012 345 ", brand="X", model="Y")
        assert t.imei == "123456789012345"

    def test_removes_dots_and_slashes(self):
        t = TrackerCreate(imei="12345/678.90-12345", brand="X", model="Y")
        assert t.imei == "123456789012345"

    def test_null_bytes_stripped(self):
        # Null bytes are non-digits and should be filtered out
        t = TrackerCreate(imei="\x0012345\x00", brand="X", model="Y")
        assert t.imei == "12345"

    def test_exactly_5_digits_accepted(self):
        t = TrackerCreate(imei="12345", brand="X", model="Y")
        assert t.imei == "12345"

    def test_fewer_than_5_digits_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            TrackerCreate(imei="1234", brand="X", model="Y")
        assert "inválido" in str(exc_info.value).lower() or "value error" in str(exc_info.value).lower()

    def test_zero_digits_rejected(self):
        with pytest.raises(ValidationError):
            TrackerCreate(imei="ABCDE", brand="X", model="Y")

    def test_sql_injection_in_imei_is_sanitized(self):
        # SQL injection leaves only digits → valid if ≥ 5 digits
        t = TrackerCreate(imei="1'; DROP TABLE trackers; --12345", brand="X", model="Y")
        assert t.imei.isdigit()
        assert len(t.imei) >= 5

    def test_sql_injection_too_short_rejected(self):
        with pytest.raises(ValidationError):
            TrackerCreate(imei="'; DROP TABLE trackers; --", brand="X", model="Y")

    def test_oversized_imei_accepted_after_normalization(self):
        # 100-digit IMEI normalized to digits — should pass (no upper bound)
        long_imei = "1" * 100
        t = TrackerCreate(imei=long_imei, brand="X", model="Y")
        assert t.imei == long_imei

    def test_update_none_imei_passthrough(self):
        u = TrackerUpdate(imei=None)
        assert u.imei is None

    def test_update_valid_imei_normalized(self):
        u = TrackerUpdate(imei="12-345")
        assert u.imei == "12345"

    def test_update_too_short_rejected(self):
        with pytest.raises(ValidationError):
            TrackerUpdate(imei="123")


# ---------------------------------------------------------------------------
# Text field normalization
# ---------------------------------------------------------------------------

class TestTextNormalization:
    def test_brand_stripped(self):
        t = TrackerCreate(imei="12345", brand="  Teltonika  ", model="FMB920")
        assert t.brand == "Teltonika"

    def test_model_stripped(self):
        t = TrackerCreate(imei="12345", brand="X", model="  FMB920  ")
        assert t.model == "FMB920"

    def test_notes_stripped(self):
        t = TrackerCreate(imei="12345", brand="X", model="Y", notes="  some note  ")
        assert t.notes == "some note"

    def test_empty_string_becomes_none(self):
        t = TrackerCreate(imei="12345", brand="X", model="Y", notes="   ")
        assert t.notes is None

    def test_none_stays_none(self):
        t = TrackerCreate(imei="12345", brand="X", model="Y", notes=None)
        assert t.notes is None

    def test_xss_in_notes_stored_as_is(self):
        payload = "<script>alert('xss')</script>"
        t = TrackerCreate(imei="12345", brand="X", model="Y", notes=payload)
        assert t.notes == payload

    def test_path_traversal_in_notes_stored_as_is(self):
        t = TrackerCreate(imei="12345", brand="X", model="Y", notes="../../etc/passwd")
        assert t.notes == "../../etc/passwd"

    def test_unicode_in_brand(self):
        t = TrackerCreate(imei="12345", brand="Tèst™ Ñoño", model="Y")
        assert t.brand == "Tèst™ Ñoño"

    def test_chinese_in_notes(self):
        t = TrackerCreate(imei="12345", brand="X", model="Y", notes="中文注释")
        assert t.notes == "中文注释"

    def test_firmware_stripped(self):
        t = TrackerCreate(imei="12345", brand="X", model="Y", firmware="  v1.2.3  ")
        assert t.firmware == "v1.2.3"

    def test_ip_address_stripped(self):
        t = TrackerCreate(imei="12345", brand="X", model="Y", ip_address=" 192.168.1.1 ")
        assert t.ip_address == "192.168.1.1"

    def test_install_location_stripped(self):
        t = TrackerCreate(imei="12345", brand="X", model="Y", install_location="  Painel frontal  ")
        assert t.install_location == "Painel frontal"


# ---------------------------------------------------------------------------
# SIM / ICCID digit normalization
# ---------------------------------------------------------------------------

class TestSimNormalization:
    def test_sim_number_digits_only(self):
        t = TrackerCreate(imei="12345", brand="X", model="Y", sim_number="+55 (11) 99999-0000")
        assert t.sim_number == "5511999990000"

    def test_sim_number_empty_becomes_none(self):
        t = TrackerCreate(imei="12345", brand="X", model="Y", sim_number="")
        assert t.sim_number is None

    def test_sim_iccid_digits_only(self):
        # "8955 1234 5678 9012 345" → 4+4+4+4+3 = 19 digits
        t = TrackerCreate(imei="12345", brand="X", model="Y", sim_iccid="8955 1234 5678 9012 345")
        assert t.sim_iccid == "8955123456789012345"

    def test_sim_iccid_none_stays_none(self):
        t = TrackerCreate(imei="12345", brand="X", model="Y", sim_iccid=None)
        assert t.sim_iccid is None

    def test_sim_number_letters_stripped(self):
        t = TrackerCreate(imei="12345", brand="X", model="Y", sim_number="ABC123DEF456")
        assert t.sim_number == "123456"


# ---------------------------------------------------------------------------
# TrackerLinkPayload
# ---------------------------------------------------------------------------

class TestTrackerLinkPayload:
    def test_valid_payload(self):
        p = TrackerLinkPayload(vehicle_id=1)
        assert p.vehicle_id == 1
        assert p.auto_generate_billings is True
        assert p.billing_cycles == 60       # default mudou para boleto (60 meses)
        assert p.billing_modality == 'boleto'
        assert p.installation_fee == 0.0
        assert p.generate_prorated is True

    def test_billing_day_min(self):
        p = TrackerLinkPayload(vehicle_id=1, billing_day=1)
        assert p.billing_day == 1

    def test_billing_day_max(self):
        """31 é válido — normalize_due_date() ajusta nos meses mais curtos."""
        p = TrackerLinkPayload(vehicle_id=1, billing_day=31)
        assert p.billing_day == 31

    def test_billing_day_zero_rejected(self):
        with pytest.raises(ValidationError):
            TrackerLinkPayload(vehicle_id=1, billing_day=0)

    def test_billing_day_32_rejected(self):
        with pytest.raises(ValidationError):
            TrackerLinkPayload(vehicle_id=1, billing_day=32)

    def test_billing_day_negative_rejected(self):
        with pytest.raises(ValidationError):
            TrackerLinkPayload(vehicle_id=1, billing_day=-1)

    def test_billing_cycles_min(self):
        p = TrackerLinkPayload(vehicle_id=1, billing_cycles=1)
        assert p.billing_cycles == 1

    def test_billing_cycles_max(self):
        # Limite agora é 999 (carnê de longo prazo ou boleto recorrente)
        p = TrackerLinkPayload(vehicle_id=1, billing_cycles=999)
        assert p.billing_cycles == 999

    def test_billing_cycles_zero_rejected(self):
        with pytest.raises(ValidationError):
            TrackerLinkPayload(vehicle_id=1, billing_cycles=0)

    def test_billing_cycles_above_max_rejected(self):
        with pytest.raises(ValidationError):
            TrackerLinkPayload(vehicle_id=1, billing_cycles=1000)

    def test_billing_modality_boleto(self):
        p = TrackerLinkPayload(vehicle_id=1, billing_modality='boleto')
        assert p.billing_modality == 'boleto'

    def test_billing_modality_carne(self):
        p = TrackerLinkPayload(vehicle_id=1, billing_modality='carne', billing_cycles=12)
        assert p.billing_modality == 'carne'
        assert p.billing_cycles == 12

    def test_installation_fee_positivo(self):
        p = TrackerLinkPayload(vehicle_id=1, installation_fee=80.0)
        assert p.installation_fee == 80.0

    def test_installation_fee_negativo_rejeitado(self):
        with pytest.raises(ValidationError):
            TrackerLinkPayload(vehicle_id=1, installation_fee=-1.0)

    def test_start_date_defaults_to_today(self):
        p = TrackerLinkPayload(vehicle_id=1)
        assert p.start_date == date.today()

    def test_start_date_custom(self):
        p = TrackerLinkPayload(vehicle_id=1, start_date=date(2025, 6, 1))
        assert p.start_date == date(2025, 6, 1)


# ---------------------------------------------------------------------------
# TrackerStatus enum
# ---------------------------------------------------------------------------

class TestTrackerStatusEnum:
    @pytest.mark.parametrize("status", [
        TrackerStatus.STOCK,
        TrackerStatus.INSTALLED,
        TrackerStatus.MAINTENANCE,
        TrackerStatus.LOST,
        TrackerStatus.DISCARDED,
    ])
    def test_valid_statuses(self, status):
        t = TrackerCreate(imei="12345", brand="X", model="Y", status=status)
        assert t.status == status

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            TrackerCreate(imei="12345", brand="X", model="Y", status="voando")

    def test_default_status_is_stock(self):
        t = TrackerCreate(imei="12345", brand="X", model="Y")
        assert t.status == TrackerStatus.STOCK


# ---------------------------------------------------------------------------
# TrackerUpdate: all optional
# ---------------------------------------------------------------------------

class TestTrackerUpdate:
    def test_empty_update_valid(self):
        u = TrackerUpdate()
        assert u.imei is None
        assert u.brand is None

    def test_partial_update(self):
        u = TrackerUpdate(brand="NewBrand")
        assert u.brand == "NewBrand"
        assert u.model is None

    def test_xss_in_update_notes(self):
        u = TrackerUpdate(notes="<img src=x onerror=alert(1)>")
        assert "<img" in u.notes

    def test_sql_injection_update_brand(self):
        u = TrackerUpdate(brand="'; DROP TABLE trackers; --")
        assert u.brand == "'; DROP TABLE trackers; --"
