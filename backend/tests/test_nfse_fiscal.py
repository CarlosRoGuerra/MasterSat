"""Testes da integração dos dados fiscais do cliente na emissão de NFS-e."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

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
