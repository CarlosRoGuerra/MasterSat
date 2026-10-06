"""Forma de cobrança do cliente e boleto SGR unificado no relatório de cobranças.

Pedido de 06/10/2026: o relatório mostrava o boleto SGR 2976 da TRANSPORTADORA
LINDOMAR em 24 linhas (uma por placa) e "Valor Pago" em branco; e os filtros
BOLETO MENSAL / CARNÊ AILOS / CARNÊ SIMPLES / CARTÃO DE CRÉDITO precisam
existir no cadastro do cliente, no relatório e na simulação do fechamento.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus, ClientStatus
from app.services.billing_closure import simulate_closure

REPORT = '/api/v1/exports/billings-report'


def _cliente(db, nome, doc, forma=None):
    c = Client(name=nome, cpf_cnpj=doc, type='pj', status=ClientStatus.ACTIVE, forma_cobranca=forma)
    db.add(c)
    db.commit()
    return c


def _cobranca(db, cliente, valor, titulo, *, cod=None, pago=True):
    b = Billing(
        client_id=cliente.id, amount=Decimal(valor), title=titulo, billing_type='recorrente',
        due_date=date(2026, 9, 21), period_label='09/2026',
        status=BillingStatus.PAID if pago else BillingStatus.PENDING,
        payment_date=date(2026, 9, 28) if pago else None,
        sgr_payload={'cod_boleto': cod} if cod else None,
    )
    db.add(b)
    db.commit()
    return b


def _linhas(r):
    return [l for l in r.text.strip().splitlines() if l.strip()][1:]


def test_boleto_sgr_sai_em_uma_linha_com_total_e_valor_pago(http, db):
    lindomar = _cliente(db, 'TRANSPORTADORA LINDOMAR LTDA', '11111111000111')
    for placa in ('SXE7D98', 'RYT7E61', 'MEY2E87'):
        _cobranca(db, lindomar, '64.99', f'Boleto SGR 2976 - {placa}', cod='2976')
    _cobranca(db, lindomar, '50.00', 'Avulsa da casa')
    r = http.get(REPORT, params={'fmt': 'csv', 'situacao': 'paga', 'periodo_por': 'pagamento'})
    assert r.status_code == 200
    linhas = _linhas(r)
    assert len(linhas) == 2
    sgr = next(l for l in linhas if 'SGR 2976' in l)
    assert '3 placa(s)' in sgr and '194.97' in sgr
    assert sgr.count('194.97') == 2  # valor e valor pago (pago pelo SGR, sem paid_amount)


def test_boleto_sgr_com_placas_em_situacoes_diferentes_fica_parcial(http, db):
    c = _cliente(db, 'CLIENTE SGR', '22222222000122')
    _cobranca(db, c, '64.99', 'Boleto SGR 9 - AAA1A11', cod='9')
    _cobranca(db, c, '64.99', 'Boleto SGR 9 - BBB2B22', cod='9', pago=False)
    r = http.get(REPORT, params={'fmt': 'csv', 'situacao': 'todas', 'periodo_por': 'vencimento'})
    (linha,) = _linhas(r)
    assert 'Parcial' in linha and '2 placa(s)' in linha


def test_pdf_do_relatorio_agrupado_gera(http, db):
    c = _cliente(db, 'TRANSPORTADORA LINDOMAR LTDA', '11111111000111', 'boleto_mensal')
    for placa in ('SXE7D98', 'RYT7E61'):
        _cobranca(db, c, '64.99', f'Boleto SGR 2976 - {placa}', cod='2976')
    r = http.get(REPORT, params={'fmt': 'pdf', 'situacao': 'paga', 'periodo_por': 'pagamento',
                                 'forma_cobranca': 'boleto_mensal'})
    assert r.status_code == 200 and r.content[:5] == b'%PDF-'


@pytest.mark.parametrize('forma, esperado', [
    ('boleto_mensal', {'BOLETO'}), ('carne_ailos', {'CARNE'}), ('nao_informado', {'SEM FORMA'}),
])
def test_relatorio_filtra_pela_forma_do_responsavel(http, db, forma, esperado):
    _cobranca(db, _cliente(db, 'BOLETO', '33333333000133', 'boleto_mensal'), '10', 'a')
    _cobranca(db, _cliente(db, 'CARNE', '44444444000144', 'carne_ailos'), '10', 'b')
    _cobranca(db, _cliente(db, 'SEM FORMA', '55555555000155'), '10', 'c')
    r = http.get(REPORT, params={'fmt': 'csv', 'situacao': 'paga', 'periodo_por': 'pagamento',
                                 'forma_cobranca': forma})
    assert {l.split(';')[0].strip('"') for l in _linhas(r)} == esperado


def test_relatorio_recusa_forma_desconhecida(http):
    assert http.get(REPORT, params={'fmt': 'csv', 'forma_cobranca': 'pix'}).status_code == 422


def test_cadastro_grava_e_valida_a_forma_de_cobranca(http, db, cliente):
    r = http.put(f'/api/v1/clients/{cliente.id}', json={'forma_cobranca': 'carne_simples'})
    assert r.status_code == 200, r.text
    assert r.json()['forma_cobranca'] == 'carne_simples'
    assert http.put(f'/api/v1/clients/{cliente.id}', json={'forma_cobranca': 'boleto'}).status_code == 422
    r = http.put(f'/api/v1/clients/{cliente.id}', json={'forma_cobranca': None})
    assert r.json()['forma_cobranca'] is None


def test_simulacao_do_fechamento_filtra_pela_forma(db, plan):
    boleto = _cliente(db, 'BOLETO', '33333333000133', 'boleto_mensal')
    carne = _cliente(db, 'CARNE', '44444444000144', 'carne_ailos')
    for c in (boleto, carne):
        db.add(Contract(client_id=c.id, plan_id=plan.id, start_date=date(2025, 1, 15),
                        status='ativo', billing_day=15))
    db.commit()

    def clientes(forma):
        sim = simulate_closure(db, date(2025, 5, 1), forma_cobranca=forma)
        return {i['client_name'] for i in sim['items']}

    assert clientes(None) == {'BOLETO', 'CARNE'}
    assert clientes('boleto_mensal') == {'BOLETO'}
    assert clientes('carne_ailos') == {'CARNE'}
    assert clientes('cartao_credito') == set()


def test_rota_de_simulacao_aceita_a_forma(http, db, plan):
    c = _cliente(db, 'CARNE', '44444444000144', 'carne_ailos')
    db.add(Contract(client_id=c.id, plan_id=plan.id, start_date=date(2025, 1, 15),
                    status='ativo', billing_day=15))
    db.commit()
    r = http.get('/api/v1/billing-closure/simulate',
                 params={'reference_month': '2025-05', 'forma_cobranca': 'boleto_mensal'})
    assert r.status_code == 200, r.text
    assert r.json()['items'] == []
    r = http.get('/api/v1/billing-closure/simulate',
                 params={'reference_month': '2025-05', 'forma_cobranca': 'carne_ailos'})
    assert len(r.json()['items']) == 1
