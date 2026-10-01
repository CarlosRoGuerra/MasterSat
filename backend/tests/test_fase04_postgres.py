"""Fase 04 — migration a4c7e2f9b1d6 e importador SGR em PostgreSQL real.

Migration: aditiva (não toca cobrança importada), downgrade guarda as tabelas
com dados como fase04_preservado_* e o upgrade seguinte as devolve.
Importador: índices parciais, JSON e commit por unidade no banco de verdade.
"""
from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import inspect, text

from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus
from app.models.sgr_migracao import SgrConflito, SgrDocumento, SgrExecucao, SgrExecucaoUnidade
from app.services.sgr_migration import importer
from tests.test_database_concurrency_postgres import postgres_api  # noqa: F401 — fixture
from tests.test_fase02_migrations_postgres import (  # noqa: F401 — fixture e helpers
    _alembic,
    _billing,
    _revisao,
    _seed_base,
    banco,
)
from tests.test_fase04_sgr import CPF_B, _mensalidade, boleto, importar, no, veiculo

pytestmark = pytest.mark.postgres
FASE03 = 'b7d3e1f5a902'
FASE04 = 'a4c7e2f9b1d6'
TABELAS = ('sgr_execucoes', 'sgr_execucao_unidades', 'sgr_vinculos', 'sgr_documentos',
           'sgr_documento_linhas', 'sgr_conflitos', 'sgr_arquivos')


def _tabelas(engine) -> set[str]:
    with engine.connect() as conn:
        return set(inspect(conn).get_table_names())


def test_upgrade_nao_toca_cobranca_importada_e_downgrade_preserva(banco):
    _alembic(banco, 'upgrade', FASE03)
    payload = {'cod_boleto': '777', 'valor': '80,00', 'discriminacao': [
        {'valor': '100,00', 'placa': 'ABC1234'}, {'valor': '-20,00', 'placa': 'ABC1234'}]}
    with banco.begin() as conn:
        ids = _seed_base(conn)
        legado = _billing(conn, ids, billing_type='avulsa', amount=100)
        conn.execute(text('UPDATE billings SET sgr_payload = CAST(:p AS JSON) WHERE id = :b'),
                     {'p': json.dumps(payload), 'b': legado})
        antes = conn.execute(text('SELECT id, amount, status, sgr_payload::text FROM billings')).all()

    _alembic(banco, 'upgrade', FASE04)
    assert set(TABELAS) <= _tabelas(banco)
    with banco.begin() as conn:
        assert conn.execute(text('SELECT id, amount, status, sgr_payload::text FROM billings')).all() == antes
        exec_id = conn.execute(text(
            "INSERT INTO sgr_execucoes (status) VALUES ('concluida') RETURNING id")).scalar_one()
        conn.execute(text(
            "INSERT INTO sgr_documentos (cod_boleto, status, criado_por, ultima_execucao_id) "
            "VALUES ('777', 'bloqueado', 'backfill', :e)"), {'e': exec_id})
        conn.execute(text(
            "INSERT INTO sgr_vinculos (entidade, chave_origem, local_id, criado_por) VALUES ('cliente', '1', :c, 'adocao')"
        ), {'c': ids['cliente']})

    _alembic(banco, 'downgrade', FASE03)
    assert _revisao(banco) == FASE03
    tabelas = _tabelas(banco)
    assert not set(TABELAS) & tabelas
    assert {f'fase04_preservado_{t}' for t in TABELAS} <= tabelas
    with banco.connect() as conn:
        assert conn.execute(text('SELECT id, amount, status, sgr_payload::text FROM billings')).all() == antes

    _alembic(banco, 'upgrade', FASE04)
    with banco.connect() as conn:
        assert conn.execute(text('SELECT cod_boleto FROM sgr_documentos')).scalars().all() == ['777']
        assert conn.execute(text('SELECT count(*) FROM sgr_vinculos')).scalar_one() == 1
    assert not any(t.startswith('fase04_preservado_') for t in _tabelas(banco))


def test_downgrade_sem_dados_remove_as_tabelas(banco):
    _alembic(banco, 'upgrade', FASE04)
    _alembic(banco, 'downgrade', FASE03)
    tabelas = _tabelas(banco)
    assert not any(t.startswith('sgr_') or t.startswith('fase04_') for t in tabelas)
    _alembic(banco, 'upgrade', FASE04)
    assert set(TABELAS) <= _tabelas(banco)


def test_indice_de_conflito_aberto_e_unico_no_banco(banco):
    _alembic(banco, 'upgrade', FASE04)
    sql = ("INSERT INTO sgr_conflitos (entidade, chave_origem, tipo, status) "
           "VALUES ('veiculo', '10', 'transferencia_veiculo', :s)")
    with banco.begin() as conn:
        conn.execute(text(sql), {'s': 'resolvido'})
        conn.execute(text(sql), {'s': 'aberto'})
    with pytest.raises(Exception, match='uq_sgr_conflitos_aberto'):
        with banco.begin() as conn:
            conn.execute(text(sql), {'s': 'aberto'})


# ---------------------------------------------------------------------------
# Importador no PostgreSQL
# ---------------------------------------------------------------------------

def test_fluxo_completo_no_postgres(postgres_api):
    _http, sessions, _engine = postgres_api
    with sessions() as db:
        stats = importar(db, no(boletos=[boleto(9001, [('100,00', 'ABC1234', 'MENSALIDADE'),
                                                      ('-20,00', 'ABC1234', 'DESCONTO')])]))
        assert stats.manifesto['diferencas'] == []
        assert [b.amount for b in db.query(Billing)] == [80]

    with sessions() as db:  # pagamento entre rodadas
        importar(db, no(boletos=[boleto(9001, [('100,00', 'ABC1234', 'MENSALIDADE'),
                                              ('-20,00', 'ABC1234', 'DESCONTO')],
                                        situacao='BAIXADO', pago='80,00', data_pag='10/10/2026')]))
    with sessions() as db:
        cobranca = db.query(Billing).one()
        assert (cobranca.status, cobranca.payment_date) == (BillingStatus.PAID, date(2026, 10, 10))

    with sessions() as db:  # mesma placa em outro cliente, duas rodadas: um conflito aberto só
        for _ in range(2):
            importar(db, no(cod='2', cpf=CPF_B, veiculos=[veiculo('20', 'ABC1234')]))
        assert db.query(SgrConflito).filter_by(status='aberto', tipo='transferencia_veiculo').count() == 1
        assert db.query(Client).count() == 2


def test_falha_de_uma_unidade_isolada_e_simulacao_sem_rastro(postgres_api, monkeypatch):
    _http, sessions, _engine = postgres_api
    original = importer._import_invoices

    def quebra(ctx, stats, node, client):
        if node.mapped['external_id'] == '2':
            raise RuntimeError('falha simulada')
        return original(ctx, stats, node, client)
    monkeypatch.setattr(importer, '_import_invoices', quebra)
    with sessions() as db:
        stats = importar(db, no(cod='1', boletos=[_mensalidade(9401)]),
                         no(cod='2', cpf=CPF_B, veiculos=[veiculo('20', 'DEF5678')]))
        assert stats.status_execucao == 'incompleta'
    with sessions() as db:
        assert db.query(Client).count() == 1
        unidades = {u.unidade: u.status for u in db.query(SgrExecucaoUnidade)}
        assert unidades == {'planos': 'aplicada', 'cliente:1': 'aplicada', 'cliente:2': 'falhou'}

    monkeypatch.setattr(importer, '_import_invoices', original)
    with sessions() as db:
        antes = (db.query(SgrExecucao).count(), db.query(SgrDocumento).count(), db.query(Billing).count())
        importar(db, no(cod='3', cpf='12345678909', veiculos=[veiculo('30', 'GHI9012')],
                        boletos=[_mensalidade(9501)]), dry_run=True)
    with sessions() as db:
        assert (db.query(SgrExecucao).count(), db.query(SgrDocumento).count(), db.query(Billing).count()) == antes
