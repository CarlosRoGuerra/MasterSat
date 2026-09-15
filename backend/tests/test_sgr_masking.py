"""Testes de mascaramento de dados sensíveis da POC de migração SGR (ETAPA 13)."""
from __future__ import annotations

from app.services.sgr_migration.masking import (
    mask_document,
    mask_email,
    mask_imei,
    mask_phone,
)


class TestMaskDocument:
    def test_keeps_last_two_digits(self):
        assert mask_document('721.307.080-05') == '*********05'

    def test_empty_returns_empty(self):
        assert mask_document('') == ''
        assert mask_document(None) == ''


class TestMaskPhone:
    def test_keeps_last_four_digits(self):
        assert mask_phone('31999998888') == '*******8888'

    def test_short_value_fully_masked(self):
        assert mask_phone('12') == '**'


class TestMaskImei:
    def test_keeps_last_four_digits(self):
        assert mask_imei('355488020902005') == '***********2005'

    def test_accepts_non_string(self):
        assert mask_imei(355488020902005) == '***********2005'


class TestMaskEmail:
    def test_keeps_first_char_and_domain(self):
        assert mask_email('joao@email.com') == 'j***@email.com'

    def test_empty_returns_empty(self):
        assert mask_email(None) == ''

    def test_no_at_sign_fully_masked(self):
        assert mask_email('not-an-email') == '***'
