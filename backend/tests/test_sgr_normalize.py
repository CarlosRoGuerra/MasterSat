"""Testes de normalização/validação puras usadas pela POC de migração SGR."""
from __future__ import annotations

from app.services.sgr_migration.normalize import (
    is_valid_cnpj,
    is_valid_cpf,
    is_valid_cpf_cnpj,
    is_valid_email,
    is_valid_plate,
    normalize_cpf_cnpj,
    normalize_email,
    normalize_phone,
    normalize_plate,
    only_digits,
    parse_br_date,
)


class TestOnlyDigits:
    def test_strips_non_digits(self):
        assert only_digits('721.307.080-05') == '72130708005'

    def test_none_returns_empty(self):
        assert only_digits(None) == ''


class TestCpfCnpj:
    def test_valid_cpf(self):
        assert is_valid_cpf('11144477735') is True

    def test_invalid_cpf_checksum(self):
        assert is_valid_cpf('11144477736') is False

    def test_cpf_all_same_digit_is_invalid(self):
        assert is_valid_cpf('11111111111') is False

    def test_valid_cnpj(self):
        assert is_valid_cnpj('11222333000181') is True

    def test_invalid_cnpj_checksum(self):
        assert is_valid_cnpj('11222333000182') is False

    def test_normalize_detects_pf_by_length(self):
        digits, tipo = normalize_cpf_cnpj('721.307.080-05')
        assert digits == '72130708005'
        assert tipo == 'pf'

    def test_normalize_detects_pj_by_length(self):
        digits, tipo = normalize_cpf_cnpj('11.222.333/0001-81')
        assert tipo == 'pj'

    def test_normalize_unknown_length_returns_none_type(self):
        _digits, tipo = normalize_cpf_cnpj('123')
        assert tipo is None

    def test_is_valid_cpf_cnpj_dispatches_by_type(self):
        assert is_valid_cpf_cnpj('11144477735', 'pf') is True
        assert is_valid_cpf_cnpj('11222333000181', 'pj') is True
        assert is_valid_cpf_cnpj('11144477735', 'pj') is False
        assert is_valid_cpf_cnpj('123', None) is False


class TestPhone:
    def test_strips_ddi_55_when_long_enough(self):
        assert normalize_phone('5531999999999') == '31999999999'

    def test_keeps_number_without_ddi(self):
        assert normalize_phone('31999999999') == '31999999999'

    def test_none_returns_empty(self):
        assert normalize_phone(None) == ''


class TestPlate:
    def test_removes_hyphen_and_spaces(self):
        assert normalize_plate('abc-1234') == 'ABC1234'

    def test_valid_old_format_plate(self):
        assert is_valid_plate('ABC1234') is True

    def test_valid_mercosul_plate(self):
        assert is_valid_plate('ABC1D23') is True

    def test_invalid_plate_shape(self):
        assert is_valid_plate('AB123') is False


class TestEmail:
    def test_lowercases_and_strips(self):
        assert normalize_email(' JOAO@EMAIL.COM ') == 'joao@email.com'

    def test_valid_email(self):
        assert is_valid_email('joao@email.com') is True

    def test_invalid_email_missing_domain_dot(self):
        assert is_valid_email('joao@email') is False


class TestDate:
    def test_parses_br_format(self):
        assert parse_br_date('10/03/1995').isoformat() == '1995-03-10'

    def test_parses_iso_format(self):
        assert parse_br_date('1995-03-10').isoformat() == '1995-03-10'

    def test_returns_none_for_garbage(self):
        assert parse_br_date('not-a-date') is None

    def test_returns_none_for_empty(self):
        assert parse_br_date(None) is None
        assert parse_br_date('') is None
