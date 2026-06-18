"""
Testes para app.services.ailos_boletos.aplicar_dados_oficiais_ailos — override
dos dados calculados localmente pelos dados oficiais retornados pela API Ailos.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.ailos_boleto import AilosBoleto
from app.services.ailos_boletos import aplicar_dados_oficiais_ailos
from app.services.boleto_ailos import gerar_dados_boleto
from app.services.boleto_pdf import gerar_boleto_pdf


def _dados_base():
    return gerar_dados_boleto(
        billing_id=1,
        valor=Decimal('99.90'),
        vencimento=date(2027, 6, 30),
        sacado_nome='Cliente Teste',
        sacado_cpf_cnpj='12345678901',
        sacado_endereco='Rua Teste, 100',
        data_emissao=date(2027, 6, 1),
    )


class TestAplicarDadosOficiaisAilos:
    def test_sem_ailos_boleto_retorna_dados_inalterados(self):
        dados = _dados_base()

        resultado = aplicar_dados_oficiais_ailos(dados, None)

        assert resultado is dados

    def test_ailos_boleto_sem_dados_oficiais_retorna_inalterado(self):
        dados = _dados_base()
        ailos_boleto = AilosBoleto(billing_id=1, numero_convenio='102004')

        resultado = aplicar_dados_oficiais_ailos(dados, ailos_boleto)

        assert resultado is dados

    def test_ailos_boleto_com_dados_oficiais_sobrescreve(self):
        dados = _dados_base()
        ailos_boleto = AilosBoleto(
            billing_id=1,
            numero_convenio='102004',
            nosso_numero='99999999',
            linha_digitavel='LINHA-OFICIAL',
            codigo_barras='CODIGO-OFICIAL',
        )

        resultado = aplicar_dados_oficiais_ailos(dados, ailos_boleto)

        assert resultado is not dados
        assert resultado.codigo_barras == 'CODIGO-OFICIAL'
        assert resultado.linha_digitavel == 'LINHA-OFICIAL'
        assert resultado.nosso_numero_display == '99999999'

        # Demais campos preservados
        assert resultado.billing_id == dados.billing_id
        assert resultado.valor == dados.valor
        assert resultado.sacado_nome == dados.sacado_nome
        assert resultado.data_vencimento == dados.data_vencimento

    def test_pdf_gerado_com_dados_oficiais_e_valido(self):
        dados = _dados_base()
        ailos_boleto = AilosBoleto(
            billing_id=1,
            numero_convenio='102004',
            nosso_numero='99999999',
            linha_digitavel='LINHA-OFICIAL',
            codigo_barras='CODIGO-OFICIAL',
        )

        resultado = aplicar_dados_oficiais_ailos(dados, ailos_boleto)
        pdf_bytes = gerar_boleto_pdf(resultado)

        assert pdf_bytes[:4] == b'%PDF'
