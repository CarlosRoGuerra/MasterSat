"""Vetores publicados nos manuais fornecidos, sem chamadas ao banco."""
from datetime import date
from decimal import Decimal

import pytest

from scripts.sicredi_homologacao_pdf import (codigo_barras, fator_vencimento, linha_digitavel,
                                            crc_pix, gerar_pix_demonstrativo)


def test_codigo_e_linha_conferem_com_response_do_manual_api_pagina_46():
    # Dados decodificados do exemplo publicado, sem usar o gerador como oráculo.
    codigo = codigo_barras('0512', '03', '15335', '251006142', date(2022, 1, 13), Decimal('99.90'))
    assert codigo == '74891886400000099901125100614205120315335103'
    assert linha_digitavel(codigo) == '74891.12511 00614.205128 03153.351030 1 88640000009990'


@pytest.mark.parametrize(('dia', 'esperado'), [(date(2007, 12, 20), 3726),
                                             (date(2025, 2, 21), 9999),
                                             (date(2025, 2, 22), 1000),
                                             (date(2025, 2, 23), 1001)])
def test_fator_de_vencimento_conforme_cnab_400_pagina_46(dia, esperado):
    assert fator_vencimento(dia) == esperado


def test_campo_livre_codifica_convenio_em_vez_do_convenio_padrao_do_sandbox():
    codigo = codigo_barras('2602', '13', '11948', '269000019', date(2026, 11, 13), Decimal('10.00'))
    assert len(codigo) == 44
    assert codigo[19:21] == '11'
    assert codigo[21:30] == '269000019'
    assert codigo[30:34] == '2602'
    assert codigo[34:36] == '13'
    assert codigo[36:41] == '11948'
    assert codigo[41:43] == '10'
    assert codigo[9:19] == '0000001000'
    assert len(linha_digitavel(codigo).replace('.', '').replace(' ', '')) == 47


def test_linha_digitavel_recusa_codigo_corrompido():
    codigo = '74891886400000099901125100614205120315335103'
    with pytest.raises(ValueError, match='DV geral'):
        linha_digitavel(codigo[:4] + '9' + codigo[5:])


@pytest.mark.parametrize('valor', ['0', '-1', 'NaN', 'Infinity', '1.001', '100000000.00'])
def test_valor_nao_e_truncado_para_caber_no_codigo_de_barras(valor):
    with pytest.raises(ValueError, match='Valor incompatível'):
        codigo_barras('2602', '13', '11948', '269000019', date(2026, 11, 13), Decimal(valor))


def test_crc_pix_confere_com_vetor_publicado_no_manual_qr_code():
    # Manual QR Code Cobrança, folha 21 (22ª página do PDF). CRC independente.
    payload = ('00020126910014br.gov.bcb.pix2569pix-qrcode.sicredi.com.br/qr/v2/cobv/'
               'c1f7e23631cd49c8be2ec56551f129775204000053039865802BR5903PIX6006Cidade'
               '62070503***6304')
    assert crc_pix(payload) == '8A39'


def _ler_tlv(payload):
    campos = {}
    posicao = 0
    while posicao < len(payload):
        campo, tamanho = payload[posicao:posicao + 2], int(payload[posicao + 2:posicao + 4])
        campos[campo] = payload[posicao + 4:posicao + 4 + tamanho]
        assert len(campos[campo]) == tamanho
        posicao += 4 + tamanho
    assert posicao == len(payload)
    return campos


def test_pix_dinamico_demo_usa_location_homologacao_aleatoria_e_valor_do_boleto():
    import re

    primeiro = gerar_pix_demonstrativo(Decimal('10.00'))
    segundo = gerar_pix_demonstrativo(Decimal('10.00'))
    campos = _ler_tlv(primeiro)
    conta = _ler_tlv(campos['26'])
    assert campos['00'] == '01' and campos['01'] == '12'
    assert conta['00'] == 'br.gov.bcb.pix'
    assert re.fullmatch(r'pix-qrcode-h\.sicredi\.com\.br/qr/v2/cobv/[0-9a-f]{32}', conta['25'])
    assert len(conta['25']) <= 77
    assert '01' not in conta  # Sem chave Pix arbitrária de terceiro.
    assert campos['54'] == '10.00' and campos['53'] == '986'
    assert campos['58'] == 'BR' and campos['59'] == 'MASTERSAT'
    assert _ler_tlv(campos['62'])['05'] == '***'
    assert campos['63'] == crc_pix(primeiro[:-4])
    assert primeiro != segundo
