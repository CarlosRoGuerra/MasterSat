"""Dia de vencimento herdado ao vincular rastreador com plano.

Caso real: cliente migrado do SGR sem dia no cadastro (o SGR guarda o dia no
vínculo). O contrato antigo vencia dia 10; refeito pela tela de rastreador com
início em 29/01/2025, saiu com vencimento dia 28 (dia do início, limitado a 28).
"""
from __future__ import annotations

from datetime import date

import pytest

from app.models.contract import Contract
from app.models.vehicle import Vehicle
from app.services.dia_vencimento import sugerir_dia_vencimento

PREFIX = '/api/v1/trackers'


def _contrato(db, cliente, plan, veiculo, dia, status='ativo'):
    c = Contract(client_id=cliente.id, plan_id=plan.id, vehicle_id=veiculo.id,
                 start_date=date(2025, 1, 29), status=status, billing_day=dia)
    db.add(c)
    db.commit()
    return c


def _outro_veiculo(db, cliente, placa):
    v = Vehicle(client_id=cliente.id, plate=placa, brand='VW', model='Gol', year=2020)
    db.add(v)
    db.commit()
    return v


def test_cadastro_do_cliente_tem_prioridade(db, cliente, plan, veiculo):
    cliente.billing_day = 5
    _contrato(db, cliente, plan, veiculo, 10)
    assert sugerir_dia_vencimento(db, cliente.id, veiculo.id)['dia'] == 5


def test_contrato_anterior_encerrado_do_veiculo(db, cliente, plan, veiculo):
    antigo = _contrato(db, cliente, plan, veiculo, 10, status='inativo')
    s = sugerir_dia_vencimento(db, cliente.id, veiculo.id)
    assert (s['dia'], s['origem'], s['contrato_id']) == (10, 'contrato_anterior', antigo.id)


def test_contratos_ativos_do_cliente_valem_para_veiculo_novo(db, cliente, plan, veiculo):
    _contrato(db, cliente, plan, _outro_veiculo(db, cliente, 'AAA1A11'), 15)
    _contrato(db, cliente, plan, _outro_veiculo(db, cliente, 'BBB2B22'), 15)
    _contrato(db, cliente, plan, _outro_veiculo(db, cliente, 'CCC3C33'), 20)
    s = sugerir_dia_vencimento(db, cliente.id, veiculo.id)
    assert (s['dia'], s['origem'], s['alternativas']) == (15, 'contrato_ativo', [20])


def test_contrato_ativo_do_proprio_veiculo_vence_o_mais_frequente(db, cliente, plan, veiculo):
    _contrato(db, cliente, plan, _outro_veiculo(db, cliente, 'AAA1A11'), 15)
    _contrato(db, cliente, plan, _outro_veiculo(db, cliente, 'BBB2B22'), 15)
    _contrato(db, cliente, plan, veiculo, 20)
    assert sugerir_dia_vencimento(db, cliente.id, veiculo.id)['dia'] == 20


def test_sem_nada_nao_inventa_dia(db, cliente, veiculo):
    assert sugerir_dia_vencimento(db, cliente.id, veiculo.id) == {
        'dia': None, 'origem': None, 'contrato_id': None, 'alternativas': [],
    }


def test_vinculo_sem_dia_herda_do_contrato_antigo_e_nao_do_inicio(http, db, cliente, plan, veiculo, rastreador):
    _contrato(db, cliente, plan, veiculo, 10, status='inativo')
    r = http.post(f'{PREFIX}/{rastreador.id}/link-vehicle', json={
        'vehicle_id': veiculo.id, 'plan_id': plan.id, 'start_date': '2025-01-29',
    })
    assert r.status_code == 200, r.text
    assert r.json()['contract']['billing_day'] == 10  # antes: 28


def test_dia_informado_na_tela_prevalece(http, db, cliente, plan, veiculo, rastreador):
    _contrato(db, cliente, plan, veiculo, 10, status='inativo')
    r = http.post(f'{PREFIX}/{rastreador.id}/link-vehicle', json={
        'vehicle_id': veiculo.id, 'plan_id': plan.id, 'start_date': '2025-01-29', 'billing_day': 7,
    })
    assert r.json()['contract']['billing_day'] == 7


def test_rota_de_sugestao(http, http_op, db, cliente, plan, veiculo):
    _contrato(db, cliente, plan, veiculo, 10, status='inativo')
    r = http_op.get(f'{PREFIX}/billing-day-suggestion', params={'vehicle_id': veiculo.id})
    assert r.status_code == 200, r.text
    assert r.json()['dia'] == 10 and r.json()['origem'] == 'contrato_anterior'
    assert http.get(f'{PREFIX}/billing-day-suggestion', params={'vehicle_id': 999999}).status_code == 404


@pytest.mark.parametrize('dia_inicio, esperado', [(20, 20), (31, 28)])
def test_sem_historico_mantem_regra_do_dia_de_inicio(http, db, plan, veiculo, rastreador, dia_inicio, esperado):
    r = http.post(f'{PREFIX}/{rastreador.id}/link-vehicle', json={
        'vehicle_id': veiculo.id, 'plan_id': plan.id, 'start_date': f'2025-01-{dia_inicio:02d}',
    })
    assert r.json()['contract']['billing_day'] == esperado
