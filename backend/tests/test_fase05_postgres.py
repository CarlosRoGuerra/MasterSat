"""Concorrência multiprocesso e rollback aditivo em PostgreSQL descartável.

TEST_DATABASE_URL deve apontar para servidor exclusivo de testes. Transportes
fiscais são dublês locais; os processos não usam certificados reais.
"""
from __future__ import annotations

import base64
from datetime import date, timedelta
from decimal import Decimal
import gzip
import multiprocessing
from queue import Empty
import traceback
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest
import requests
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus, ClientStatus
from app.models.nfse_nota import NfseNota
from app.services import nfse_emissao
from app.services import nfse_lote
from tests.test_database_concurrency_postgres import postgres_api  # noqa: F401
from tests.test_fase02_migrations_postgres import banco, _alembic, _billing, _seed_base, _revisao  # noqa: F401

pytestmark = pytest.mark.postgres
ANTERIOR = 'a4c7e2f9b1d6'
FASE05 = 'f5a1c8e3d902'


def _cobranca(sessions):
    with sessions() as db:
        client = Client(name='Tomador sintético', cpf_cnpj='52998224725', type='pf',
                        status=ClientStatus.ACTIVE, issue_invoice='sim',
                        zip_code='89201100', address_line='Rua teste', address_number='10',
                        neighborhood='Centro', city='Joinville', state='SC')
        db.add(client)
        db.flush()
        billing = Billing(client_id=client.id, amount=Decimal('89.90'),
                          due_date=date(2026, 10, 10), status=BillingStatus.PENDING,
                          billing_type='avulsa', period_label='07/2026', title='Título original')
        db.add(billing)
        db.commit()
        return billing.id


def _emitir_processo(url, schema, bid, start, sent, release, results):
    """Alvo top-level para spawn: memória, engine e identidade não são herdados."""
    from app.core.config import settings
    from app.services import nfse_nacional
    from app.models import registry_all  # noqa: F401

    engine = create_engine(url, connect_args={'options': f'-csearch_path={schema}'})
    settings.nfse_enabled = True
    settings.nfse_nac_ambiente = 'producao_restrita'

    def post(path, payload, **kwargs):
        results.put(('post', path))
        sent.set()
        if not release.wait(15):
            raise RuntimeError('barreira do teste não liberou POST')
        xml = (f'<NFSe xmlns="{nfse_nacional.NS_NFSE}"><infNFSe Id="NFS' + '1' * 50 + '">'
               f'<nNFSe>1234</nNFSe><DPS><infDPS Id="{nfse_nacional.id_dps(str(bid))}"/>'
               '</DPS></infNFSe></NFSe>')
        response = requests.Response()
        response.status_code = 201
        import json
        response._content = json.dumps({'nfseXmlGZipB64': base64.b64encode(
            gzip.compress(xml.encode())).decode()}).encode()
        return response

    try:
        with Session(engine) as db, patch.object(nfse_nacional, 'assinar_dps', lambda dps: dps), \
                patch.object(nfse_nacional, '_post', post):
            billing = db.get(Billing, bid)
            client = db.get(Client, billing.client_id)
            client.city_ibge_code = '4209102'  # dado sintético; não consulta ViaCEP
            start.wait(15)
            nota = nfse_nacional.emitir_nfse(db, billing, client,
                                           competencia=date(2024, 2, 1),
                                           discriminacao='Descrição exclusiva — fevereiro 2024')
            results.put(('result', nota.status))
    except Exception:
        results.put(('exception', traceback.format_exc()))
        raise
    finally:
        engine.dispose()


def test_dois_processos_um_post_e_restart_parcial_preserva_lease(postgres_api):
    _, sessions, engine = postgres_api
    bid = _cobranca(sessions)
    with engine.connect() as conn:
        schema = conn.scalar(text('SELECT current_schema()'))
    context = multiprocessing.get_context('spawn')
    start, sent, release = [context.Event() for _ in range(3)]
    results = context.Queue()
    args = (engine.url.render_as_string(hide_password=False), schema, bid, start, sent, release, results)
    workers = [context.Process(target=_emitir_processo, args=args) for _ in range(2)]
    messages = []
    try:
        for worker in workers:
            worker.start()
        start.set()
        assert sent.wait(15), 'nenhum processo chegou ao transporte simulado'
        # Simula o boot de um terceiro worker enquanto a emissão está em voo.
        with sessions() as db:
            assert nfse_emissao.recuperar_expiradas(db) == []
            nota = db.query(NfseNota).one()
            assert nota.status == 'processing'
            assert nota.emissor_id and nota.heartbeat_at and nota.lease_expires_at
        while not any(kind == 'result' for kind, _ in messages):
            messages.append(results.get(timeout=15))
            assert not any(kind == 'exception' for kind, _ in messages), messages
        assert [value for kind, value in messages if kind == 'result'] == ['processing']
        release.set()
        for worker in workers:
            worker.join(15)
            assert worker.exitcode == 0, messages
        while True:
            try:
                messages.append(results.get(timeout=.2))
            except Empty:
                break
        assert [v for k, v in messages if k == 'post'] == ['/nfse'], messages
        with sessions() as db:
            nota = db.query(NfseNota).one()
            assert nota.status == 'emitida'
            assert nota.tentativa_numero == 1
            assert nota.competencia == date(2024, 2, 1)
            assert 'Descrição exclusiva — fevereiro 2024' in nota.xml_envio
    finally:
        release.set()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(5)
        results.close()


def test_expirada_consulta_e_resposta_tardia_nao_sobrescreve_autorizacao(postgres_api):
    _, sessions, _ = postgres_api
    bid = _cobranca(sessions)
    with sessions() as db:
        nota, token = nfse_emissao.reservar(db, bid, provedor='nacional', ambiente='producao_restrita')
        nid = nota.id
        assert nfse_emissao.marcar_envio(db, nid, token, '<DPS/>')
        nota.lease_expires_at = nfse_emissao.agora(db) - timedelta(seconds=1)
        db.commit()
    with sessions() as db:
        assert nfse_emissao.recuperar_expiradas(db) == [nid]
        nota, novo_envio = nfse_emissao.reservar(db, bid)
        assert novo_envio is None and nota.status == 'desconhecido'
        consulta = nfse_emissao.reservar_consulta(db, nota)
        assert consulta and consulta != token
        nfse_emissao.atualizar(db, nid, consulta, status='emitida', numero_nfse='54321', xml_retorno='<NFSe/>')
    with sessions() as db:
        nota = nfse_emissao.atualizar(db, nid, token, status='erro', erro_mensagem='resposta tardia')
        assert (nota.status, nota.numero_nfse, nota.xml_retorno) == ('emitida', '54321', '<NFSe/>')


def test_lotes_concorrentes_nao_roubam_nota(postgres_api):
    _, sessions, _ = postgres_api
    bid = _cobranca(sessions)
    start = Barrier(2)

    def criar():
        with sessions() as db:
            start.wait(5)
            try:
                return nfse_lote.criar_lote(db, '07/2026', [bid], emitir_async=False).id
            except nfse_lote.LoteError:
                return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(criar) for _ in range(2)]
        resultados = [f.result(timeout=15) for f in futures]
    assert sum(value is not None for value in resultados) == 1
    with sessions() as db:
        nota = db.query(NfseNota).one()
        assert nota.lote_id == next(value for value in resultados if value is not None)
        assert nota.status == 'pending' and nota.tentativa_numero == 0


def test_heartbeat_renova_em_sessao_independente(postgres_api, monkeypatch):
    _, sessions, _ = postgres_api
    bid = _cobranca(sessions)
    monkeypatch.setattr(nfse_emissao, 'HEARTBEAT_INTERVAL_SECONDS', .05)
    with sessions() as db:
        nota, token = nfse_emissao.reservar(db, bid)
        anterior = nota.heartbeat_at
        with nfse_emissao.heartbeat(db, nota.id, token):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with sessions() as leitor:
                    atual = leitor.get(NfseNota, nota.id)
                    if atual.heartbeat_at > anterior:
                        assert atual.lease_expires_at > nota.lease_expires_at
                        break
                time.sleep(.05)
            else:
                pytest.fail('heartbeat não foi persistido')


def test_migration_backfill_preserva_documentos_rollback_e_guardas(banco):
    _alembic(banco, 'upgrade', ANTERIOR)
    with banco.begin() as conn:
        ids = _seed_base(conn)
        for status in ('pending', 'processing', 'erro', 'emitida'):
            bid = _billing(conn, ids, billing_type='avulsa', amount=Decimal('89.90'))
            conn.execute(text("""
                INSERT INTO nfse_notas (billing_id, status, numero_rps, serie_rps,
                    numero_lote, protocolo, numero_nfse, serie_nfse, chave_acesso,
                    codigo_verificacao, xml_envio, xml_retorno, erro_mensagem)
                VALUES (:bid, :status, '101', '40000', '100', 'PROTOCOLO',
                    '99', '1', 'CHAVE', 'CODIGO', '<DPS>á</DPS>', '<NFSe>ç</NFSe>', 'original')
            """), {'bid': bid, 'status': status})
        original = conn.execute(text('SELECT id, billing_id, numero_rps, serie_rps, numero_lote, '
                                    'protocolo, numero_nfse, serie_nfse, chave_acesso, codigo_verificacao, '
                                    'xml_envio, xml_retorno, erro_mensagem FROM nfse_notas ORDER BY id')).all()
    _alembic(banco, 'upgrade', FASE05)
    with banco.connect() as conn:
        assert conn.execute(text('SELECT status FROM nfse_notas ORDER BY id')).scalars().all() == [
            'desconhecido', 'desconhecido', 'desconhecido', 'emitida']
        assert conn.scalar(text('SELECT count(*) FROM nfse_notas WHERE tentativa_id IS NOT NULL')) == 0
    for sql in (
        "UPDATE nfse_notas SET status='pending' WHERE status='emitida'",
        "UPDATE nfse_notas SET xml_retorno='<alterado/>' WHERE status='emitida'",
        "UPDATE nfse_notas SET status='pending' WHERE status='desconhecido'",
    ):
        with pytest.raises(Exception, match='NFS-e'):
            with banco.begin() as conn:
                conn.execute(text(sql))
    _alembic(banco, 'downgrade', ANTERIOR)
    assert _revisao(banco) == ANTERIOR
    with banco.connect() as conn:
        assert 'tentativa_id' in {c['name'] for c in inspect(conn).get_columns('nfse_notas')}
        after = conn.execute(text('SELECT id, billing_id, numero_rps, serie_rps, numero_lote, '
                                 'protocolo, numero_nfse, serie_nfse, chave_acesso, codigo_verificacao, '
                                 'xml_envio, xml_retorno, erro_mensagem FROM nfse_notas ORDER BY id')).all()
        assert after == original
    _alembic(banco, 'upgrade', FASE05)
    with banco.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM nfse_notas WHERE status='desconhecido'")) == 3
