"""Fase 03 — FIN-03: resultado bancário desconhecido não libera a cobrança.

Timeout depois do envio, 5xx, falha local depois do aceite e reserva órfã
viram DESFECHO_DESCONHECIDO: nada muda valor/estado nem reemite até a
consulta pelo número do documento resolver.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import requests

from app.models.ailos_api_log import AilosApiLog
from app.models.ailos_boleto import AilosBoleto
from app.models.billing_change_log import BillingChangeLog
from app.services import ailos_boletos
from tests.fase03_apoio import ailos_falsa  # noqa: F401 — fixture
from tests.fase03_apoio import boleto_json, cobranca, preencher_endereco, reserva, resp

A = '/api/v1/ailos'
B = '/api/v1/billings'


def _status(db, billing_id):
    db.expire_all()
    return db.query(AilosBoleto).filter_by(billing_id=billing_id).one().status_ailos


class TestClassificacaoDaFalha:
    def test_timeout_de_leitura_apos_envio_bloqueia_edicao_e_reemissao(self, http, db, contrato, cliente, ailos_falsa):
        preencher_endereco(db, cliente)
        b = cobranca(db, contrato)
        chamadas = ailos_falsa(lambda m, u, **k: requests.ReadTimeout('read timed out'))

        r = http.post(f'{A}/boletos', json={'billing_id': b.id})
        assert r.status_code == 502
        assert r.json()['detail']['code'] == 'desfecho_desconhecido'
        assert _status(db, b.id) == 'DESFECHO_DESCONHECIDO'

        put = http.put(f'{B}/{b.id}', json={'amount': 130, 'justification': 'ajuste'})
        assert put.status_code == 409
        db.refresh(b)
        assert b.amount == Decimal('100.00')

        # Nova tentativa não reenvia o POST: a cobrança está bloqueada.
        de_novo = http.post(f'{A}/boletos', json={'billing_id': b.id})
        assert de_novo.status_code == 409
        assert [m for m, _ in chamadas] == ['POST']

    def test_falha_de_conexao_antes_do_envio_libera(self, http, db, contrato, cliente, ailos_falsa):
        preencher_endereco(db, cliente)
        b = cobranca(db, contrato)
        ailos_falsa(lambda m, u, **k: requests.ConnectTimeout('connect timed out'))
        assert http.post(f'{A}/boletos', json={'billing_id': b.id}).status_code == 400
        assert _status(db, b.id) == 'ERRO_REGISTRO'
        assert http.put(f'{B}/{b.id}', json={'amount': 130, 'justification': 'x'}).status_code == 200

    def test_5xx_e_desconhecido_4xx_e_recusa(self, http, db, contrato, cliente, ailos_falsa):
        preencher_endereco(db, cliente)
        b5 = cobranca(db, contrato)
        b4 = cobranca(db, contrato)
        respostas = {b5.id: resp(503, {'mensagem': 'indisponível'}), b4.id: resp(422, {'mensagem': 'CEP inválido'})}
        ailos_falsa(lambda m, u, json=None, **k: respostas[json['documento']['numeroDocumento']])
        assert http.post(f'{A}/boletos', json={'billing_id': b5.id}).status_code == 502
        assert http.post(f'{A}/boletos', json={'billing_id': b4.id}).status_code == 502
        assert _status(db, b5.id) == 'DESFECHO_DESCONHECIDO'
        assert _status(db, b4.id) == 'ERRO_REGISTRO'

    def test_falha_do_log_depois_do_aceite_nao_desfaz_o_registro(self, http, db, contrato, cliente, ailos_falsa, monkeypatch):
        from app.services import ailos_client

        preencher_endereco(db, cliente)
        b = cobranca(db, contrato)
        ailos_falsa(lambda m, u, **k: resp(200, boleto_json(b.id)))

        def log_quebrado(*_a, **_k):
            raise RuntimeError('banco de log fora')

        monkeypatch.setattr(ailos_client, '_log_call', log_quebrado)
        r = http.post(f'{A}/boletos', json={'billing_id': b.id})
        assert r.status_code == 200
        assert r.json()['linha_digitavel'] == f'LD{b.id}'

    def test_falha_local_depois_do_aceite_vira_desfecho_desconhecido(self, db, contrato, cliente, ailos_falsa, monkeypatch):
        preencher_endereco(db, cliente)
        b = cobranca(db, contrato)
        ailos_falsa(lambda m, u, **k: resp(200, boleto_json(b.id)))

        def upsert_quebrado(*_a, **_k):
            raise RuntimeError('disco cheio')

        monkeypatch.setattr(ailos_boletos, '_upsert_ailos_boleto', upsert_quebrado)
        try:
            ailos_boletos.gerar_boleto(db, b, cliente)
        except Exception as exc:  # noqa: BLE001
            assert type(exc).__name__ == 'AilosDesfechoDesconhecido'
        else:
            raise AssertionError('deveria sinalizar desfecho desconhecido')
        assert _status(db, b.id) == 'DESFECHO_DESCONHECIDO'

    def test_timeout_no_lote_marca_todas_as_parcelas(self, http, db, contrato, cliente, ailos_falsa):
        preencher_endereco(db, cliente)
        a, b = cobranca(db, contrato), cobranca(db, contrato)
        ailos_falsa(lambda m, u, **k: requests.ReadTimeout('read timed out'))
        r = http.post(f'{A}/boletos/lote', json={'billing_ids': [a.id, b.id]})
        assert r.status_code == 502
        assert sorted(r.json()['detail']['billing_ids']) == sorted([a.id, b.id])
        assert _status(db, a.id) == _status(db, b.id) == 'DESFECHO_DESCONHECIDO'


class TestResolucao:
    def test_consulta_encontra_o_titulo(self, http, db, contrato, ailos_falsa):
        b = cobranca(db, contrato)
        reserva(db, b, 'DESFECHO_DESCONHECIDO')
        ailos_falsa(lambda m, u, **k: resp(200, boleto_json(b.id)))
        r = http.post(f'{A}/boletos/{b.id}/consultar-desfecho')
        assert r.json()['resultado'] == 'registrado'
        assert http.get(f'{B}/{b.id}').json()['titulo_bancario']['estado'] == 'registrado'

    def test_ausencia_so_conclui_depois_do_prazo(self, http, db, contrato, ailos_falsa):
        recente = cobranca(db, contrato)
        reserva(db, recente, 'DESFECHO_DESCONHECIDO', minutos_atras=2)
        antiga = cobranca(db, contrato)
        reserva(db, antiga, 'DESFECHO_DESCONHECIDO', minutos_atras=20)
        ailos_falsa(lambda m, u, **k: resp(404, {'mensagem': 'Boleto não encontrado'}))

        assert http.post(f'{A}/boletos/{recente.id}/consultar-desfecho').json()['resultado'] == 'aguardar'
        assert _status(db, recente.id) == 'DESFECHO_DESCONHECIDO'

        assert http.post(f'{A}/boletos/{antiga.id}/consultar-desfecho').json()['resultado'] == 'nao_registrado'
        assert _status(db, antiga.id) == 'ERRO_REGISTRO'
        assert db.query(BillingChangeLog).filter_by(billing_id=antiga.id, field_name='registro_ailos').count() == 1
        assert http.put(f'{B}/{antiga.id}', json={'amount': 90, 'justification': 'x'}).status_code == 200

    def test_consulta_indisponivel_mantem_bloqueio(self, http, db, contrato, ailos_falsa):
        b = cobranca(db, contrato)
        reserva(db, b, 'DESFECHO_DESCONHECIDO', minutos_atras=60)
        ailos_falsa(lambda m, u, **k: resp(500, {'mensagem': 'erro'}))
        assert http.post(f'{A}/boletos/{b.id}/consultar-desfecho').json()['resultado'] == 'indisponivel'
        assert _status(db, b.id) == 'DESFECHO_DESCONHECIDO'

    def test_retry_manual_consulta_antes_de_reenviar(self, http, db, contrato, cliente, ailos_falsa):
        from app.models.ailos_lote import AilosLote

        preencher_endereco(db, cliente)
        b = cobranca(db, contrato)
        lote = AilosLote(tipo='carne', ticket='T-1', numero_convenio='102004', billing_ids=[b.id], status='completed')
        db.add(lote)
        db.commit()
        reserva(db, b, 'PROCESSANDO', minutos_atras=40)
        chamadas = ailos_falsa(lambda m, u, **k: resp(200, boleto_json(b.id)))
        r = http.post(f'{A}/lotes/{lote.id}/parcelas/{b.id}/registrar')
        assert r.status_code == 200
        assert [m for m, _ in chamadas] == ['GET']  # achou pela consulta; nenhum POST

    def test_declarar_nao_registrado_nao_e_do_financeiro(self, http_fin, db, contrato):
        # (http e http_fin no mesmo teste compartilham o usuário — ver Fase 00)
        b = cobranca(db, contrato)
        reserva(db, b, 'DESFECHO_DESCONHECIDO')
        assert http_fin.post(f'{A}/boletos/{b.id}/declarar-nao-registrado',
                             json={'justificativa': 'conferido no banco'}).status_code == 403

    def test_declarar_nao_registrado_e_administrativo_e_auditado(self, http, db, contrato):
        b = cobranca(db, contrato)
        reserva(db, b, 'DESFECHO_DESCONHECIDO')
        r = http.post(f'{A}/boletos/{b.id}/declarar-nao-registrado', json={'justificativa': 'conferido no banco'})
        assert r.status_code == 200
        assert _status(db, b.id) == 'ERRO_REGISTRO'
        log = db.query(BillingChangeLog).filter_by(billing_id=b.id, field_name='registro_ailos').one()
        assert 'conferido no banco' in log.justification and log.changed_by_user_id == 1

    def test_reserva_orfa_resolvida_pela_conciliacao(self, db, contrato, ailos_falsa):
        b = cobranca(db, contrato)
        boleto = reserva(db, b, 'REGISTRANDO')
        boleto.registro_iniciado_em = datetime.now(timezone.utc) - timedelta(hours=2)
        db.commit()
        ailos_falsa(lambda m, u, **k: resp(200, boleto_json(b.id)))
        resultado = ailos_boletos.conciliar_boletos_abertos(db, limit=10)
        assert resultado['desfechos_resolvidos'] == 1
        db.refresh(boleto)
        assert boleto.linha_digitavel == f'LD{b.id}'
        assert db.query(AilosApiLog).count() >= 1
