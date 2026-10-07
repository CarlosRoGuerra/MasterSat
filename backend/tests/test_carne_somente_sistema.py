"""Carnê só no sistema (pedido de 06/10/2026).

Além do carnê registrado na Ailos, o carnê simples: as parcelas ficam só no
sistema e nunca podem ser emitidas no banco — nem pela tela de detalhes, nem
em lote, nem como carnê Ailos, nem por remessa CNAB.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.enums import BillingStatus
from app.services import cnab_remessa, titulo_bancario

PARCELAR = '/api/v1/billings/parcelar'
AILOS = '/api/v1/ailos'


@pytest.fixture
def cliente_com_endereco(db, cliente):
    cliente.zip_code = '89201-000'
    cliente.address_line = 'Rua Principal'
    cliente.address_number = '100'
    cliente.neighborhood = 'Centro'
    cliente.city = 'Joinville'
    cliente.state = 'SC'
    db.commit()
    return cliente


def _parcelar(http, contrato, **extra):
    r = http.post(PARCELAR, json={
        'contract_id': contrato.id, 'num_parcelas': 3, 'primeiro_vencimento': '2099-01-15', **extra,
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_carne_do_sistema_marca_as_parcelas_e_a_forma_do_cliente(http, db, contrato, cliente):
    parcelas = _parcelar(http, contrato, somente_sistema=True)
    assert [p['somente_sistema'] for p in parcelas] == [True, True, True]
    assert {p['billing_type'] for p in parcelas} == {'carne'}
    db.refresh(cliente)
    assert cliente.forma_cobranca == 'carne_simples'


def test_carne_ailos_continua_como_antes(http, db, contrato, cliente):
    parcelas = _parcelar(http, contrato)
    assert not any(p['somente_sistema'] for p in parcelas)
    db.refresh(cliente)
    assert cliente.forma_cobranca == 'carne_ailos'


def test_forma_ja_escolhida_no_cadastro_nao_e_sobrescrita(http, db, contrato, cliente):
    cliente.forma_cobranca = 'boleto_mensal'
    db.commit()
    _parcelar(http, contrato, somente_sistema=True)
    db.refresh(cliente)
    assert cliente.forma_cobranca == 'boleto_mensal'


@pytest.mark.parametrize('rota, corpo', [
    ('boletos', lambda ids: {'billing_id': ids[0]}),
    ('boletos/lote', lambda ids: {'billing_ids': ids}),
    ('carne/lote', lambda ids: {'billing_ids': ids}),
])
def test_nenhuma_emissao_na_ailos_aceita_parcela_do_sistema(
    http, db, contrato, cliente_com_endereco, rota, corpo,
):
    ids = [p['id'] for p in _parcelar(http, contrato, somente_sistema=True)]
    with patch('app.services.ailos_boletos.ailos_client.request') as ailos:
        r = http.post(f'{AILOS}/{rota}', json=corpo(ids))
    assert r.status_code == 409, r.text
    assert 'somente no sistema' in r.text
    ailos.assert_not_called()
    assert db.query(AilosBoleto).filter(AilosBoleto.billing_id.in_(ids)).count() == 0


def test_carne_misturando_parcela_do_sistema_e_recusado_inteiro(http, db, contrato, cliente_com_endereco):
    sistema = _parcelar(http, contrato, somente_sistema=True)[0]['id']
    avulsa = Billing(client_id=cliente_com_endereco.id, amount=Decimal('64.99'), due_date=date(2099, 6, 10),
                     status=BillingStatus.PENDING, billing_type='avulsa', title='Avulsa')
    db.add(avulsa)
    db.commit()
    with patch('app.services.ailos_boletos.ailos_client.request') as ailos:
        r = http.post(f'{AILOS}/carne/lote', json={'billing_ids': [avulsa.id, sistema]})
    assert r.status_code == 409
    ailos.assert_not_called()


def test_politica_recusa_emitir_e_libera_o_resto(http, db, contrato):
    ids = [p['id'] for p in _parcelar(http, contrato, somente_sistema=True)]
    for operacao in (titulo_bancario.EMITIR_AILOS, titulo_bancario.EMITIR_CNAB):
        with pytest.raises(titulo_bancario.PoliticaBancariaError) as exc:
            titulo_bancario.exigir(db, operacao, ids)
        assert exc.value.code == 'cobranca_somente_sistema' and exc.value.billing_ids == ids
    # Receber, ajustar, cancelar e excluir seguem a regra de quem não tem título.
    for operacao in (titulo_bancario.RECEBER, titulo_bancario.ALTERAR_VALOR,
                     titulo_bancario.CANCELAR, titulo_bancario.EXCLUIR):
        titulo_bancario.exigir(db, operacao, ids)


def test_remessa_cnab_ignora_e_recusa_parcela_do_sistema(http, db, contrato):
    ids = [p['id'] for p in _parcelar(http, contrato, somente_sistema=True)]
    assert not set(ids) & {b.id for b in cnab_remessa.selecionar_por_status(db, BillingStatus.PENDING)}
    with pytest.raises(cnab_remessa.RemessaError) as exc:
        cnab_remessa.validar_selecao(db, ids)
    assert set(exc.value.extra['motivos'].values()) == {'somente_sistema'}


def test_recebimento_manual_funciona(http, db, contrato):
    parcela = _parcelar(http, contrato, somente_sistema=True)[0]
    r = http.post(f'/api/v1/billings/{parcela["id"]}/receive', json={
        'payment_date': '2099-01-15', 'payment_method': 'dinheiro',
    })
    assert r.status_code == 200, r.text
    assert r.json()['status'] == 'paga' and r.json()['somente_sistema'] is True


def test_negociacao_de_parcelas_do_sistema_continua_fora_do_banco(http, db, contrato):
    ids = [p['id'] for p in _parcelar(http, contrato, somente_sistema=True)][:2]
    r = http.post('/api/v1/billings/unificar', json={'billing_ids': ids, 'due_date': '2099-03-15'})
    assert r.status_code == 200, r.text
    assert r.json()['somente_sistema'] is True


def test_detalhe_da_cobranca_informa_a_marca(http, contrato):
    parcela = _parcelar(http, contrato, somente_sistema=True)[0]
    assert http.get(f'/api/v1/billings/{parcela["id"]}').json()['somente_sistema'] is True
