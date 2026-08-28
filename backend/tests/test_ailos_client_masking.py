"""Testes para o mascaramento de segredos/PII nos payloads da Ailos (SEC-06)."""
from __future__ import annotations

from app.services.ailos_client import _mask_payload


class TestMaskSensitiveKeys:
    def test_masks_token_and_password_style_keys(self):
        payload = {'authorization': 'Bearer abcdef123456', 'senha': 'segredo123'}
        masked = _mask_payload(payload)
        assert masked['authorization'] == '***************3456'
        assert masked['senha'] == '******o123'


class TestMaskDocument:
    def test_masks_cpf_cnpj_keeping_last_two_digits(self):
        payload = {'identificadorReceitaFederal': '12345678900'}
        masked = _mask_payload(payload)
        assert masked['identificadorReceitaFederal'] == '*********00'


class TestMaskName:
    def test_masks_name_keeping_first_word(self):
        payload = {'nome': 'Maria Aparecida Souza'}
        masked = _mask_payload(payload)
        assert masked['nome'] == 'Maria ***'

    def test_single_word_name_kept_unmasked(self):
        payload = {'nome': 'Maria'}
        masked = _mask_payload(payload)
        assert masked['nome'] == 'Maria'


class TestMaskAddressAndContact:
    def test_masks_address_and_phone_but_keeps_city_and_state(self):
        payload = {
            'pagador': {
                'telefone': {'ddi': '55', 'ddd': '47', 'numero': '999998888'},
                'emails': [{'endereco': 'cliente@example.com'}],
                'endereco': {
                    'cep': '89200000',
                    'logradouro': 'Rua das Flores',
                    'numero': '123',
                    'complemento': 'apto 1',
                    'bairro': 'Centro',
                    'cidade': 'Joinville',
                    'uf': 'SC',
                },
            }
        }
        masked = _mask_payload(payload)['pagador']
        assert masked['telefone']['numero'] == '***'
        assert masked['telefone']['ddd'] == '47'
        assert masked['emails'][0]['endereco'] == '***'
        assert masked['endereco']['cep'] == '***'
        assert masked['endereco']['logradouro'] == '***'
        assert masked['endereco']['numero'] == '***'
        assert masked['endereco']['complemento'] == '***'
        assert masked['endereco']['bairro'] == '***'
        assert masked['endereco']['cidade'] == 'Joinville'
        assert masked['endereco']['uf'] == 'SC'


class TestKeepsDiagnosticData:
    def test_keeps_amounts_and_document_number(self):
        payload = {
            'documento': {'numeroDocumento': 42},
            'valorBoleto': {'valorNominal': 199.9},
        }
        masked = _mask_payload(payload)
        assert masked == payload

    def test_none_and_empty_values_untouched(self):
        payload = {'nome': None, 'cep': ''}
        masked = _mask_payload(payload)
        assert masked == payload
