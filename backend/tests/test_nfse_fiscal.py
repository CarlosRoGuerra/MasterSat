"""Testes da integração dos dados fiscais do cliente na emissão de NFS-e."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.nfse_joinville import (
    NfseError,
    _sim_nao_code,
    emitir_nfse,
    montar_inf_rps,
)


def _client(**kw):
    base = dict(
        name='CLIENTE TESTE', cpf_cnpj='00000000000191', type='pj', email='a@b.com',
        phone='4730000000', zip_code='89220000', address_line='RUA X', address_number='1',
        address_complement='', neighborhood='CENTRO', city='JOINVILLE', state='SC',
        optante_simples=None, iss_retido=None, issue_invoice=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _billing():
    return SimpleNamespace(id=1, title='MENSALIDADE', amount=Decimal('10.00'))


def test_sim_nao_code():
    assert _sim_nao_code('sim', 2) == '1'
    assert _sim_nao_code('nao', 1) == '2'
    assert _sim_nao_code(None, 2) == '2'   # cai no default global


def test_rps_usa_optante_e_iss_do_cliente():
    inf, _ = montar_inf_rps(_billing(), _client(optante_simples='nao', iss_retido='sim'), '1')
    assert inf.findtext('OptanteSimplesNacional') == '2'
    assert inf.find('Servico/Valores/IssRetido').text == '1'


def test_emitir_bloqueia_quando_cliente_nao_emite_nf():
    # gate é checado antes de qualquer acesso ao banco/SOAP → db=None é ok
    with pytest.raises(NfseError, match='NÃO emitir nota fiscal'):
        emitir_nfse(None, _billing(), _client(issue_invoice='nao'))


def test_rps_envia_grupo_pis_cofins():
    """Nota Nacional obriga TributosFederais/PisCofins com os 5 campos."""
    inf, _ = montar_inf_rps(_billing(), _client(), '1')
    grupo = inf.find('Servico/Valores/TributosFederais/PisCofins')
    assert grupo is not None
    assert grupo.findtext('CST') == settings.nfse_pis_cofins_cst
    assert grupo.findtext('ValorBaseCalculoPisCofins') == settings.nfse_pis_cofins_base
    assert grupo.findtext('PercentualAliquotaPis') == settings.nfse_aliquota_pis
    assert grupo.findtext('PercentualAliquotaCofins') == settings.nfse_aliquota_cofins
    assert grupo.findtext('TipoRetencaoPisCofins') == settings.nfse_tipo_retencao_pis_cofins


def test_rps_nao_envia_tags_descontinuadas_de_pis_cofins():
    """ValorPis/ValorCofins soltos em <Valores> geram E923."""
    inf, _ = montar_inf_rps(_billing(), _client(), '1')
    valores = inf.find('Servico/Valores')
    assert valores.find('ValorPis') is None
    assert valores.find('ValorCofins') is None


@pytest.mark.parametrize('campo,rotulo', [
    ('address_line', 'logradouro'),
    ('address_number', 'número'),
    ('neighborhood', 'bairro'),
    ('zip_code', 'CEP'),
    ('state', 'UF'),
])
def test_emitir_exige_endereco_completo_do_tomador(campo, rotulo):
    with pytest.raises(NfseError, match=rotulo):
        emitir_nfse(None, _billing(), _client(**{campo: ''}))
