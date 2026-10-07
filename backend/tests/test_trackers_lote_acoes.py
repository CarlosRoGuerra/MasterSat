"""Ações em lote na listagem de rastreadores (pedido de 06/10/2026).

Selecionar vários (ou todos) e alterar o status ou excluir — a exclusão só
vale para equipamento extraviado ou em manutenção, inclusive a individual.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.models.contract import Contract
from app.models.enums import TrackerStatus
from app.models.tracker import Tracker
from app.models.tracker_history import TrackerHistory

PREFIX = '/api/v1/trackers'


@pytest.fixture
def novo(db):
    def criar(imei, status=TrackerStatus.STOCK, **extra):
        t = Tracker(imei=imei, serial_number=imei, brand='J16', model='4G', status=status, **extra)
        db.add(t)
        db.commit()
        db.refresh(t)
        return t
    return criar


def _por_id(resposta):
    return {item['tracker_id']: item for item in resposta.json()['itens']}


def test_status_em_lote_altera_os_livres_e_explica_os_ignorados(http, db, novo, rastreador_instalado):
    a = novo('111111111111111')
    b = novo('222222222222222', TrackerStatus.MAINTENANCE)
    ja = novo('333333333333333', TrackerStatus.LOST)
    ids = [a.id, b.id, ja.id, rastreador_instalado.id, 999999, a.id]

    r = http.post(f'{PREFIX}/lote/status', json={'ids': ids, 'status': 'extraviado', 'notes': 'inventário'})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert (corpo['simulacao'], corpo['total_enviados'], corpo['aplicados'], corpo['ignorados']) == (False, 5, 2, 3)
    itens = _por_id(r)
    assert itens[a.id]['situacao'] == itens[b.id]['situacao'] == 'aplicado'
    assert itens[ja.id]['motivo'] == 'Já está com este status'
    assert 'desinstale' in itens[rastreador_instalado.id]['motivo']
    assert itens[999999]['motivo'] == 'Rastreador não encontrado'

    for t in (a, b, rastreador_instalado):
        db.refresh(t)
    assert a.status == b.status == TrackerStatus.LOST
    assert rastreador_instalado.status == TrackerStatus.INSTALLED
    hist = db.query(TrackerHistory).filter_by(tracker_id=b.id, action='status_changed').one()
    assert (hist.previous_status, hist.new_status) == ('em_manutencao', 'extraviado')
    assert hist.notes == 'Status alterado em lote — inventário'


def test_simulacao_nao_grava(http, db, novo):
    a = novo('111111111111111')
    r = http.post(f'{PREFIX}/lote/status', json={'ids': [a.id], 'status': 'em_manutencao', 'simular': True})
    assert r.json()['simulacao'] is True and r.json()['aplicados'] == 1
    db.refresh(a)
    assert a.status == TrackerStatus.STOCK
    assert db.query(TrackerHistory).filter_by(tracker_id=a.id).count() == 0


def test_status_instalado_nao_e_aceito_em_lote(http, novo):
    a = novo('111111111111111')
    r = http.post(f'{PREFIX}/lote/status', json={'ids': [a.id], 'status': 'instalado'})
    assert r.status_code == 422


def test_rastreador_preso_a_contrato_ativo_fica_de_fora(http, db, novo, cliente, plan):
    a = novo('111111111111111', TrackerStatus.MAINTENANCE)
    contrato = Contract(client_id=cliente.id, plan_id=plan.id, tracker_id=a.id,
                        start_date=date(2026, 1, 10), status='ativo', billing_day=10)
    db.add(contrato)
    db.commit()
    for rota, corpo in (('status', {'status': 'em_estoque'}), ('excluir', {})):
        r = http.post(f'{PREFIX}/lote/{rota}', json={'ids': [a.id], **corpo})
        assert r.json()['itens'][0]['motivo'] == f'Tem contrato ativo #{contrato.id}'
    db.refresh(a)
    assert a.status == TrackerStatus.MAINTENANCE and a.is_deleted is False


def test_exclusao_em_lote_so_de_extraviado_ou_em_manutencao(http, db, novo, cliente, rastreador_instalado):
    perdido = novo('111111111111111', TrackerStatus.LOST, client_id=cliente.id)
    manut = novo('222222222222222', TrackerStatus.MAINTENANCE)
    estoque = novo('333333333333333')
    descartado = novo('444444444444444', TrackerStatus.DISCARDED)
    ids = [perdido.id, manut.id, estoque.id, descartado.id, rastreador_instalado.id]

    r = http.post(f'{PREFIX}/lote/excluir', json={'ids': ids})
    assert r.status_code == 200, r.text
    assert (r.json()['aplicados'], r.json()['ignorados']) == (2, 3)
    itens = _por_id(r)
    for t in (estoque, descartado):
        assert itens[t.id]['motivo'] == 'Só é possível excluir rastreador extraviado ou em manutenção'

    for t in (perdido, manut, estoque, descartado, rastreador_instalado):
        db.refresh(t)
    assert perdido.is_deleted and manut.is_deleted
    assert perdido.client_id is None
    assert not (estoque.is_deleted or descartado.is_deleted or rastreador_instalado.is_deleted)
    hist = db.query(TrackerHistory).filter_by(tracker_id=perdido.id, action='deleted').one()
    assert hist.previous_status == 'extraviado' and hist.new_status == 'excluido'
    # Excluído some da listagem e libera o IMEI para novo cadastro.
    assert http.get(f'{PREFIX}/{perdido.id}').status_code == 404


@pytest.mark.parametrize('status', [TrackerStatus.STOCK, TrackerStatus.DISCARDED])
def test_exclusao_individual_segue_a_mesma_regra(http, db, novo, status):
    t = novo('111111111111111', status)
    r = http.delete(f'{PREFIX}/{t.id}')
    assert r.status_code == 409
    assert 'extraviado ou em manutenção' in r.json()['detail']
    db.refresh(t)
    assert t.is_deleted is False


def test_financeiro_nao_age_em_lote(http_fin, novo):
    a = novo('111111111111111', TrackerStatus.LOST)
    assert http_fin.post(f'{PREFIX}/lote/status', json={'ids': [a.id], 'status': 'em_estoque'}).status_code == 403
    assert http_fin.post(f'{PREFIX}/lote/excluir', json={'ids': [a.id]}).status_code == 403


def test_lista_vazia_e_recusada(http):
    assert http.post(f'{PREFIX}/lote/excluir', json={'ids': []}).status_code == 422
