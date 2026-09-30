"""Fase 03 — FIN-07: CNAB indisponível até homologação; ligado, remessa é
registro com sequência/hash e reserva compartilhada com a API Ailos.

Comprimento 240/400 NÃO certifica o layout: não há fixture homologada pelo
banco. Estes testes provam só a seleção, a reserva e o registro.
"""
from __future__ import annotations

import hashlib

import pytest

from app.core.config import settings
from app.models.cnab_remessa import CnabRemessa, CnabRemessaItem
from app.models.enums import BillingStatus
from tests.fase03_apoio import ailos_falsa  # noqa: F401 — fixture
from tests.fase03_apoio import boleto_json, cobranca, preencher_endereco, registrar_titulo, resp, reserva

BOL = '/api/v1/boletos'


@pytest.fixture()
def cnab_ligado(monkeypatch):
    monkeypatch.setattr(settings, 'cnab_remessa_habilitada', True)


class TestCanalDesligado:
    @pytest.mark.parametrize('layout', ['cnab240', 'cnab400'])
    def test_remessa_recusada_com_motivo(self, http, db, contrato, layout):
        b = cobranca(db, contrato)
        r = http.post(f'{BOL}/{layout}', json=[b.id])
        assert r.status_code == 409 and r.json()['detail']['code'] == 'canal_cnab_indisponivel'
        assert db.query(CnabRemessa).count() == 0

    def test_matriz_de_canais(self, http):
        canais = http.get(f'{BOL}/canais').json()
        assert canais['ailos_api']['habilitado'] is True
        assert canais['cnab240']['habilitado'] is False and canais['cnab240']['motivo']


@pytest.mark.usefixtures('cnab_ligado')
class TestCanalLigadoParaHomologacao:
    def test_selecao_repetida_paga_cancelada_ou_ja_registrada_e_recusada(self, http, db, contrato):
        paga = cobranca(db, contrato, status=BillingStatus.PAID)
        cancelada = cobranca(db, contrato, status=BillingStatus.CANCELED)
        via_api = cobranca(db, contrato)
        registrar_titulo(db, via_api)
        incerta = cobranca(db, contrato)
        reserva(db, incerta, 'DESFECHO_DESCONHECIDO')
        livre = cobranca(db, contrato)

        repetida = http.post(f'{BOL}/cnab240', json=[livre.id, livre.id])
        assert repetida.status_code == 422 and repetida.json()['detail']['repetidos'] == [livre.id]

        r = http.post(f'{BOL}/cnab240', json=[paga.id, cancelada.id, via_api.id, incerta.id, livre.id])
        assert r.status_code == 409
        assert r.json()['detail']['motivos'] == {
            str(paga.id): 'status_paga', str(cancelada.id): 'status_cancelada',
            str(via_api.id): 'titulo_registrado', str(incerta.id): 'titulo_desfecho_desconhecido',
        }
        assert db.query(CnabRemessa).count() == 0  # nada parcial

    def test_remessa_registrada_com_sequencia_hash_e_reserva(self, http, db, contrato, cliente):
        preencher_endereco(db, cliente)
        a, b = cobranca(db, contrato), cobranca(db, contrato)
        r1 = http.post(f'{BOL}/cnab240', json=[a.id])
        r2 = http.post(f'{BOL}/cnab240', json=[b.id])
        assert r1.status_code == r2.status_code == 200
        assert (r1.headers['X-Remessa-Sequencial'], r2.headers['X-Remessa-Sequencial']) == ('1', '2')
        assert r1.headers['X-Remessa-SHA256'] == hashlib.sha256(r1.content).hexdigest()

        remessa = db.get(CnabRemessa, int(r1.headers['X-Remessa-Id']))
        assert remessa.total_titulos == 1 and remessa.arquivo == r1.content
        item = db.query(CnabRemessaItem).filter_by(billing_id=a.id).one()
        assert item.status == 'reservado'

        # Baixar de novo devolve os mesmos bytes, sem nova remessa.
        again = http.get(f'{BOL}/remessas/{remessa.id}/arquivo')
        assert again.content == r1.content and db.query(CnabRemessa).count() == 2

        # O mesmo título não entra em outra remessa.
        dup = http.post(f'{BOL}/cnab240', json=[a.id])
        assert dup.status_code == 409 and dup.json()['detail']['motivos'] == {str(a.id): 'titulo_remessa_cnab'}

    def test_reserva_cnab_bloqueia_api_e_descarte_libera(self, http, db, contrato, cliente, ailos_falsa):
        preencher_endereco(db, cliente)
        b = cobranca(db, contrato)
        r = http.post(f'{BOL}/cnab400', json=[b.id])
        assert r.status_code == 200
        chamadas = ailos_falsa(lambda m, u, **k: resp(200, boleto_json(b.id)))

        api = http.post('/api/v1/ailos/boletos', json={'billing_id': b.id})
        assert api.status_code == 409 and api.json()['detail']['code'] == 'titulo_em_remessa_cnab'
        assert chamadas == []
        assert http.put(f'/api/v1/billings/{b.id}', json={'amount': 5, 'justification': 'x'}).status_code == 409

        desc = http.post(f'{BOL}/remessas/{r.headers["X-Remessa-Id"]}/descartar',
                         json={'motivo': 'arquivo não foi enviado ao banco'})
        assert desc.status_code == 200 and desc.json()['status'] == 'descartada'
        assert http.post('/api/v1/ailos/boletos', json={'billing_id': b.id}).status_code == 200

    def test_selecao_automatica_ignora_titulos_de_outro_canal(self, http, db, contrato, cliente):
        preencher_endereco(db, cliente)
        via_api = cobranca(db, contrato)
        registrar_titulo(db, via_api)
        livre = cobranca(db, contrato)
        r = http.post(f'{BOL}/cnab240?status=pendente')
        assert r.status_code == 200
        itens = [i.billing_id for i in db.query(CnabRemessaItem).all()]
        assert itens == [livre.id]
