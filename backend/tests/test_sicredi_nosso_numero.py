"""Nosso número Sicredi (manual "Geração do Nosso Número" e planilha da pasta sicred)."""
from __future__ import annotations

import pytest

from app.models.sicredi_boleto import SicrediNossoNumeroSequencia
from app.services import sicredi_nosso_numero as nn


def test_dv_confere_com_a_planilha_do_sicredi():
    # "Calculo DVs - Nosso Número.xlsx": agência 0307, posto 37, cedente 78937,
    # ano 24, byte 2, sequencial 07751 → soma 430, resto 1, 11-1=10 → DV 0.
    assert nn.montar('0307', '37', '78937', 2024, 2, 7751) == '242077510'


def test_dv_10_ou_11_vira_zero_e_demais_ficam():
    resultados = {nn.digito_verificador('6789', '03', '12345', 26, 2, seq) for seq in range(1, 200)}
    assert resultados <= set(range(10))
    assert 0 in resultados


def test_posto_alfanumerico_entra_como_00_no_calculo():
    assert nn.digito_verificador('0307', 'AB', '78937', 24, 2, 7751) == nn.digito_verificador(
        '0307', '00', '78937', 24, 2, 7751,
    )


def test_estrutura_aa_b_nnnnn_d():
    numero = nn.montar('6789', '03', '12345', 2026, 2, 1)
    assert len(numero) == 9 and numero.startswith('26200001')


@pytest.mark.parametrize('byte', [0, 1, 10])
def test_byte_fora_da_faixa_do_beneficiario(byte):
    with pytest.raises(nn.NossoNumeroError):
        nn.montar('6789', '03', '12345', 2026, byte, 1)


def test_proximo_aloca_em_sequencia_por_convenio_e_ano(db):
    chave = dict(ambiente='sandbox', cooperativa='6789', posto='03', codigo_beneficiario='12345', byte=2)
    primeiro = nn.proximo(db, ano=2026, **chave)
    segundo = nn.proximo(db, ano=2026, **chave)
    outro_ano = nn.proximo(db, ano=2027, **chave)
    db.commit()
    assert primeiro[:8] == '26200001'
    assert segundo[:8] == '26200002'
    assert outro_ano[:8] == '27200001'


def test_faixa_esgotada_avisa_para_trocar_o_byte(db):
    db.add(SicrediNossoNumeroSequencia(
        ambiente='sandbox', cooperativa='6789', posto='03', codigo_beneficiario='12345',
        ano=26, byte=2, ultimo=nn.SEQUENCIAL_MAXIMO,
    ))
    db.commit()
    with pytest.raises(nn.NossoNumeroError, match='SICREDI_NOSSO_NUMERO_BYTE'):
        nn.proximo(db, ambiente='sandbox', cooperativa='6789', posto='03', codigo_beneficiario='12345',
                   ano=2026, byte=2)
