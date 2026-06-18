"""Testes para app.services.ailos_validators (normalizações e validações puras)."""
from __future__ import annotations

import pytest

from app.services.ailos_validators import (
    AilosValidationError,
    normalize_text,
    only_digits,
    split_phone,
    strip_accents,
    validar_cpf_cnpj,
    validate_boleto_payload,
)


class TestStripAccents:
    def test_removes_diacritics(self):
        assert strip_accents('São José Açúcar') == 'Sao Jose Acucar'

    def test_no_accents_unchanged(self):
        assert strip_accents('ARARUAMA') == 'ARARUAMA'


class TestNormalizeText:
    def test_empty_or_none_returns_empty_string(self):
        assert normalize_text('', 50) == ''
        assert normalize_text(None, 50) == ''

    def test_uppercases_by_default(self):
        assert normalize_text('Cliente Teste', 50) == 'CLIENTE TESTE'

    def test_strips_accents_and_special_chars(self):
        assert normalize_text('Rúa São José, 123', 56) == 'RUA SAO JOSE 123'

    def test_collapses_repeated_spaces(self):
        assert normalize_text('Rua   das   Flores', 56) == 'RUA DAS FLORES'

    def test_truncates_to_max_length(self):
        result = normalize_text('A' * 60, 50)
        assert len(result) == 50
        assert result == 'A' * 50

    def test_upper_false_preserves_case(self):
        assert normalize_text('Teste', 10, upper=False) == 'Teste'


class TestOnlyDigits:
    def test_extracts_digits(self):
        assert only_digits('(22) 99999-9999') == '22999999999'

    def test_none_returns_empty_string(self):
        assert only_digits(None) == ''


class TestValidarCpfCnpj:
    def test_valid_cpf(self):
        assert validar_cpf_cnpj('123.456.789-01') == '12345678901'

    def test_valid_cnpj(self):
        assert validar_cpf_cnpj('12.345.678/0001-95') == '12345678000195'

    def test_missing_raises(self):
        with pytest.raises(AilosValidationError) as excinfo:
            validar_cpf_cnpj(None)
        assert excinfo.value.errors == ['CPF/CNPJ do pagador não informado']

    def test_invalid_length_raises(self):
        with pytest.raises(AilosValidationError) as excinfo:
            validar_cpf_cnpj('123')
        assert 'inválido' in excinfo.value.errors[0]


class TestSplitPhone:
    def test_empty_returns_defaults(self):
        assert split_phone(None) == ('55', '', '')
        assert split_phone('') == ('55', '', '')

    def test_local_number_without_country_code(self):
        assert split_phone('(22) 99999-9999') == ('55', '22', '999999999')

    def test_strips_leading_country_code(self):
        assert split_phone('+55 22 99999-9999') == ('55', '22', '999999999')


class TestValidateBoletoPayload:
    @staticmethod
    def _valid_payload() -> dict:
        return {
            'documento': {'descricaoDocumento': 'MENSALIDADE'},
            'emissao': {'dataEmissaoDocumento': '2026-01-01'},
            'pagador': {
                'entidadeLegal': {
                    'identificadorReceitaFederal': '12345678901',
                    'nome': 'CLIENTE TESTE',
                },
                'endereco': {
                    'cep': '12345678',
                    'uf': 'SP',
                    'logradouro': 'RUA TESTE',
                    'bairro': 'CENTRO',
                    'cidade': 'ARARUAMA',
                },
                'mensagemPagador': ['REFERENTE AO CONTRATO'],
            },
            'vencimento': {'dataVencimento': '2026-01-10'},
            'valorBoleto': {'valorNominal': 99.9},
        }

    def test_valid_payload_does_not_raise(self):
        validate_boleto_payload(self._valid_payload())

    def test_missing_cpf_cnpj_raises(self):
        payload = self._valid_payload()
        payload['pagador']['entidadeLegal']['identificadorReceitaFederal'] = ''
        with pytest.raises(AilosValidationError) as excinfo:
            validate_boleto_payload(payload)
        assert any('identificadorReceitaFederal' in e for e in excinfo.value.errors)

    def test_invalid_cep_raises(self):
        payload = self._valid_payload()
        payload['pagador']['endereco']['cep'] = '123'
        with pytest.raises(AilosValidationError) as excinfo:
            validate_boleto_payload(payload)
        assert any('cep' in e for e in excinfo.value.errors)

    def test_invalid_uf_raises(self):
        payload = self._valid_payload()
        payload['pagador']['endereco']['uf'] = 'SAO PAULO'
        with pytest.raises(AilosValidationError) as excinfo:
            validate_boleto_payload(payload)
        assert any('uf' in e for e in excinfo.value.errors)

    def test_nome_too_long_raises(self):
        payload = self._valid_payload()
        payload['pagador']['entidadeLegal']['nome'] = 'A' * 51
        with pytest.raises(AilosValidationError) as excinfo:
            validate_boleto_payload(payload)
        assert any('entidadeLegal.nome' in e for e in excinfo.value.errors)

    def test_logradouro_too_long_raises(self):
        payload = self._valid_payload()
        payload['pagador']['endereco']['logradouro'] = 'A' * 57
        with pytest.raises(AilosValidationError) as excinfo:
            validate_boleto_payload(payload)
        assert any('logradouro' in e for e in excinfo.value.errors)

    def test_bairro_too_long_raises(self):
        payload = self._valid_payload()
        payload['pagador']['endereco']['bairro'] = 'A' * 31
        with pytest.raises(AilosValidationError) as excinfo:
            validate_boleto_payload(payload)
        assert any('bairro' in e for e in excinfo.value.errors)

    def test_cidade_too_long_raises(self):
        payload = self._valid_payload()
        payload['pagador']['endereco']['cidade'] = 'A' * 31
        with pytest.raises(AilosValidationError) as excinfo:
            validate_boleto_payload(payload)
        assert any('cidade' in e for e in excinfo.value.errors)

    def test_mensagem_pagador_too_long_raises(self):
        payload = self._valid_payload()
        payload['pagador']['mensagemPagador'] = ['A' * 41]
        with pytest.raises(AilosValidationError) as excinfo:
            validate_boleto_payload(payload)
        assert any('mensagemPagador' in e for e in excinfo.value.errors)

    def test_descricao_documento_too_long_raises(self):
        payload = self._valid_payload()
        payload['documento']['descricaoDocumento'] = 'A' * 16
        with pytest.raises(AilosValidationError) as excinfo:
            validate_boleto_payload(payload)
        assert any('descricaoDocumento' in e for e in excinfo.value.errors)

    def test_valor_nominal_zero_raises(self):
        payload = self._valid_payload()
        payload['valorBoleto']['valorNominal'] = 0
        with pytest.raises(AilosValidationError) as excinfo:
            validate_boleto_payload(payload)
        assert any('valorNominal' in e for e in excinfo.value.errors)

    def test_vencimento_before_emissao_raises(self):
        payload = self._valid_payload()
        payload['vencimento']['dataVencimento'] = '2025-12-31'
        with pytest.raises(AilosValidationError) as excinfo:
            validate_boleto_payload(payload)
        assert any('dataVencimento' in e for e in excinfo.value.errors)

    def test_accumulates_all_violations(self):
        payload = self._valid_payload()
        payload['pagador']['entidadeLegal']['identificadorReceitaFederal'] = ''
        payload['pagador']['endereco']['cep'] = '123'
        payload['pagador']['endereco']['uf'] = 'X'
        with pytest.raises(AilosValidationError) as excinfo:
            validate_boleto_payload(payload)
        assert len(excinfo.value.errors) >= 3
