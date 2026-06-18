"""Testes para app.services.ailos_boletos.montar_payload_boleto (mapeamento Billing+Client → payload Ailos)."""
from __future__ import annotations

from datetime import date

import pytest

from app.core.config import settings
from app.services.ailos_boletos import montar_payload_boleto
from app.services.ailos_validators import AilosValidationError


def _preencher_endereco(cliente, db):
    cliente.zip_code = '28970-000'
    cliente.address_line = 'Rua Principal'
    cliente.address_number = '100'
    cliente.address_complement = 'Casa 2'
    cliente.neighborhood = 'Centro'
    cliente.city = 'Araruama'
    cliente.state = 'RJ'
    db.commit()
    db.refresh(cliente)


class TestMontarPayloadBoleto:
    def test_estrutura_completa_pessoa_fisica(self, db, billing_pendente, cliente):
        _preencher_endereco(cliente, db)

        payload = montar_payload_boleto(billing_pendente, cliente)

        assert payload['convenioCobranca'] == {'codigoCarteiraCobranca': settings.ailos_default_carteira}

        assert payload['documento']['numeroDocumento'] == billing_pendente.id
        assert payload['documento']['descricaoDocumento'] == 'PLANO TESTE'
        assert payload['documento']['especieDocumento'] == 2

        assert payload['emissao']['formaEmissao'] == settings.ailos_default_forma_emissao
        assert payload['emissao']['dataEmissaoDocumento'] == date.today().isoformat()

        entidade = payload['pagador']['entidadeLegal']
        assert entidade['identificadorReceitaFederal'] == '12345678901'
        assert entidade['tipoPessoa'] == 1
        assert entidade['nome'] == 'JOAO SILVA'

        assert payload['pagador']['telefone'] == {'ddi': '55', 'ddd': '11', 'numero': '999990000'}
        assert payload['pagador']['emails'] == [{'endereco': 'joao@test.local'}]

        endereco = payload['pagador']['endereco']
        assert endereco['cep'] == '28970000'
        assert endereco['logradouro'] == 'RUA PRINCIPAL'
        assert endereco['numero'] == '100'
        assert endereco['complemento'] == 'Casa 2'
        assert endereco['bairro'] == 'CENTRO'
        assert endereco['cidade'] == 'ARARUAMA'
        assert endereco['uf'] == 'RJ'

        assert payload['pagador']['mensagemPagador'] == ['REFERENTE AO CONTRATO DE RASTREAMENTO']

        assert payload['vencimento'] == {'dataVencimento': billing_pendente.due_date.isoformat()}

        assert payload['instrucoes'] == {
            'valorAbatimento': 0,
            'tipoDesconto': 3,
            'descontos': [],
            'tipoMulta': 3,
            'valorMulta': 0,
            'tipoJurosMora': 3,
            'valorJurosMora': 0,
            'diasNegativacao': 0,
            'diasProtesto': 0,
        }

        assert payload['valorBoleto'] == {'valorNominal': float(billing_pendente.amount)}

        assert payload['avisoSms'] == {
            'enviarAvisoVencimentoSms': 0,
            'enviarAvisoVencimentoSmsAntesVencimento': False,
            'enviarAvisoVencimentoSmsDiaVencimento': False,
            'enviarAvisoVencimentoSmsAposVencimento': False,
        }

        assert payload['pagamentoDivergente'] == {
            'tipoPagamentoDivergente': 0,
            'valorMinimoPagamentoDivergente': 0,
        }

        assert payload['indicadorRegistroNuclea'] == settings.ailos_default_indicador_registro_nuclea

    def test_pessoa_juridica_tipo_pessoa_2(self, db, billing_pendente, cliente):
        _preencher_endereco(cliente, db)
        cliente.type = 'pj'
        cliente.cpf_cnpj = '12345678000195'
        db.commit()

        payload = montar_payload_boleto(billing_pendente, cliente)

        assert payload['pagador']['entidadeLegal']['tipoPessoa'] == 2
        assert payload['pagador']['entidadeLegal']['identificadorReceitaFederal'] == '12345678000195'

    def test_nome_acentuado_normalizado_sem_erro(self, db, billing_pendente, cliente):
        _preencher_endereco(cliente, db)
        cliente.name = 'José da Conceição'
        db.commit()

        payload = montar_payload_boleto(billing_pendente, cliente)

        assert payload['pagador']['entidadeLegal']['nome'] == 'JOSE DA CONCEICAO'

    def test_sem_email_retorna_lista_vazia(self, db, billing_pendente, cliente):
        _preencher_endereco(cliente, db)
        cliente.email = None
        db.commit()

        payload = montar_payload_boleto(billing_pendente, cliente)

        assert payload['pagador']['emails'] == []

    def test_cpf_cnpj_vazio_propaga_validation_error(self, db, billing_pendente, cliente):
        _preencher_endereco(cliente, db)
        cliente.cpf_cnpj = ''
        db.commit()

        with pytest.raises(AilosValidationError):
            montar_payload_boleto(billing_pendente, cliente)

    def test_billing_sem_titulo_usa_mensalidade(self, db, billing_pendente, cliente):
        _preencher_endereco(cliente, db)
        billing_pendente.title = None
        db.commit()

        payload = montar_payload_boleto(billing_pendente, cliente)

        assert payload['documento']['descricaoDocumento'] == 'MENSALIDADE'
