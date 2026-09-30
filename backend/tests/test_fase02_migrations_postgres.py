"""Fase 02 — migrations, preflight e carimbo de legado em PostgreSQL real.

Cada teste cria um BANCO novo (não um schema: o Alembic, as extensões e os
tipos enum vivem no banco) no servidor de TEST_DATABASE_URL e o apaga no fim.
Nunca aponte TEST_DATABASE_URL para um banco com dados.

Cobre: DB-01 (check sem drift), DB-03 (carimbo só com schema completo), DB-04
(base → head → base → head), FIN-01/FIN-04 (backfill de competência e de
substituição, trigger para escritor antigo), DB-02 (preflight lista violações
com IDs e aborta sem alterar nada).
"""
from __future__ import annotations

import importlib.util
import os
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.db.legacy_schema import BASELINE, BASELINE_COM_REFRESH, diagnosticar_schema_legado
from app.db.session import Base
from app.models import registry_all  # noqa: F401
from app.services.competencia import competencia_do_rotulo

pytestmark = pytest.mark.postgres

BACKEND = Path(__file__).resolve().parent.parent
ANTERIOR = 'd9e4f1a7b2c5'  # head antes da Fase 02
FASE02 = 'e5c2a9d71f04'


@pytest.fixture()
def banco():
    url = os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL não configurada')
    base = make_url(url)
    nome = f'test_mig_{uuid4().hex[:12]}'
    admin = create_engine(base, isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{nome}"'))
    engine = create_engine(base.set(database=nome))
    try:
        yield engine
    finally:
        engine.dispose()
        if not nome.startswith('test_mig_'):
            raise RuntimeError('Recusa de apagar banco fora do prefixo de teste')
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{nome}" WITH (FORCE)'))
        admin.dispose()


def _alembic(engine, acao: str, revisao: str) -> None:
    cfg = Config(str(BACKEND / 'alembic.ini'))
    cfg.set_main_option('script_location', str(BACKEND / 'alembic'))
    with engine.begin() as conn:
        cfg.attributes['connection'] = conn
        getattr(command, acao)(cfg, revisao)


def _revisao(engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _colunas(engine, tabela: str) -> set[str]:
    with engine.connect() as conn:
        return {c['name'] for c in inspect(conn).get_columns(tabela)}


def _seed_base(conn) -> dict:
    """Cliente, plano, contrato e serviço mínimos, só com SQL (a revisão
    anterior não tem as colunas novas que o ORM atual conhece)."""
    cliente = conn.execute(text(
        "INSERT INTO clients (name, cpf_cnpj, type, status, is_deleted) "
        "VALUES ('Legado', '52998224725', 'pf', 'ACTIVE', false) RETURNING id"
    )).scalar_one()
    plano = conn.execute(text(
        "INSERT INTO plans (name, price, active, billing_interval_months, is_deleted) "
        "VALUES ('Plano Legado', 50, true, 1, false) RETURNING id"
    )).scalar_one()
    contrato = conn.execute(text(
        "INSERT INTO contracts (client_id, plan_id, start_date, status, billing_modality, signed, is_deleted) "
        "VALUES (:c, :p, '2026-01-01', 'ativo', 'boleto', false, false) RETURNING id"
    ), {'c': cliente, 'p': plano}).scalar_one()
    item = conn.execute(text(
        "INSERT INTO client_charge_items (client_id, title, quantity, unit_price, total_amount, "
        "installment_count, start_date, active, remove_after_payment, status, is_deleted) "
        "VALUES (:c, 'Serviço legado', 1, 90, 90, 3, '2026-01-10', false, false, 'faturado', false) RETURNING id"
    ), {'c': cliente}).scalar_one()
    return {'cliente': cliente, 'plano': plano, 'contrato': contrato, 'item': item}


def _billing(conn, ids, **campos) -> int:
    valores = {
        'client_id': ids['cliente'], 'contract_id': ids['contrato'], 'billing_type': 'recorrente',
        'amount': 50, 'due_date': date(2026, 9, 10), 'status': 'PENDING', 'is_deleted': False,
        'period_label': None, 'notes': None, 'item_id': None, 'installment_number': None,
        'installment_total': None, 'paid_amount': None,
    }
    valores.update(campos)
    cols = ', '.join(valores)
    params = ', '.join(f':{k}' for k in valores)
    return conn.execute(text(f'INSERT INTO billings ({cols}) VALUES ({params}) RETURNING id'), valores).scalar_one()


# ---------------------------------------------------------------------------
# DB-01 / DB-04
# ---------------------------------------------------------------------------

def test_upgrade_vazio_sem_drift_entre_metadata_e_migrations(banco):
    _alembic(banco, 'upgrade', 'head')
    with banco.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    # Antes: 12 remove_index, inclusive a unicidade de mensalidade (DB-01).
    assert diffs == []


def test_ciclo_completo_base_head_base_head(banco):
    _alembic(banco, 'upgrade', 'head')
    _alembic(banco, 'downgrade', 'base')
    with banco.connect() as conn:
        tipos = conn.execute(text(
            "SELECT typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = 'public' AND t.typtype = 'e'"
        )).scalars().all()
        funcoes = conn.execute(text(
            "SELECT proname FROM pg_proc WHERE proname LIKE 'mastersat_%'"
        )).scalars().all()
    assert tipos == []  # DB-04: antes sobravam os 8 enums da baseline
    assert funcoes == []
    _alembic(banco, 'upgrade', 'head')
    assert _revisao(banco) == FASE02


# ---------------------------------------------------------------------------
# Revisão anterior → head com dados legados
# ---------------------------------------------------------------------------

def test_upgrade_da_revisao_anterior_faz_backfill_e_protege_escritor_antigo(banco):
    _alembic(banco, 'upgrade', ANTERIOR)
    with banco.begin() as conn:
        ids = _seed_base(conn)
        original_a = _billing(conn, ids, period_label='09/2026', status='CANCELED')
        # O boleto único do fechamento: sem contrato.
        unico = _billing(conn, ids, contract_id=None, period_label='09/2026', amount=100)
        conn.execute(text("UPDATE billings SET notes = :n WHERE id = :id"),
                     {'n': f'Fechamento — 09/2026 | Consolidada no boleto único #{unico}.', 'id': original_a})
        trimestral = _billing(conn, ids, period_label='2026 • T4', due_date=date(2026, 10, 10))
        fora_formato = _billing(conn, ids, period_label='Setembro', billing_type='avulsa')
        negociada = _billing(conn, ids, billing_type='item', item_id=ids['item'], installment_number=1,
                             installment_total=3, period_label='01/2026', status='CANCELED')
        negociacao = _billing(conn, ids, contract_id=None, billing_type='avulsa', period_label='10/2026')
        conn.execute(text("UPDATE billings SET notes = :n WHERE id = :id"),
                     {'n': f'Unificada na cobrança #{negociacao}.', 'id': negociada})
        # Marcador apontando para cobrança que não existe: não vira vínculo.
        orfa = _billing(conn, ids, period_label='11/2026', status='CANCELED',
                        notes='Consolidada no boleto único #999999.')

    _alembic(banco, 'upgrade', 'head')

    with banco.connect() as conn:
        linhas = {
            r.id: r for r in conn.execute(text(
                'SELECT id, competencia, competencia_liberada, substituted_by_id FROM billings'
            ))
        }
    assert linhas[original_a].competencia == date(2026, 9, 1)
    assert linhas[original_a].substituted_by_id == unico
    assert linhas[trimestral].competencia == date(2026, 10, 1)
    assert linhas[fora_formato].competencia is None
    assert linhas[negociada].substituted_by_id == negociacao
    assert linhas[orfa].substituted_by_id is None
    assert not any(r.competencia_liberada for r in linhas.values())

    # Escritor que não conhece a coluna (código antigo num rollback, SQL
    # manual): o trigger calcula a competência e o índice barra '9/2026'.
    with pytest.raises(IntegrityError) as exc:
        with banco.begin() as conn:
            _billing(conn, ids, period_label='9/2026')
    assert 'uq_billings_contract_competencia_recorrente' in str(exc.value)
    with banco.begin() as conn:
        novo = _billing(conn, ids, period_label='12/2026')
        assert conn.execute(text('SELECT competencia FROM billings WHERE id = :id'),
                            {'id': novo}).scalar_one() == date(2026, 12, 1)
        # Parcela 1 do serviço já está ocupada pela original negociada.
    with pytest.raises(IntegrityError) as exc:
        with banco.begin() as conn:
            _billing(conn, ids, billing_type='item', item_id=ids['item'], installment_number=1,
                     installment_total=3, period_label='01/2026')
    assert 'uq_billings_item_parcela_efetiva' in str(exc.value)


def test_preflight_aborta_com_ids_e_nao_altera_nada(banco):
    _alembic(banco, 'upgrade', ANTERIOR)
    with banco.begin() as conn:
        ids = _seed_base(conn)
        # O índice textual antigo aceitava os dois (FIN-04).
        a = _billing(conn, ids, period_label='09/2026')
        b = _billing(conn, ids, period_label='9/2026', status='PAID', paid_amount=50)
        p1 = _billing(conn, ids, billing_type='item', item_id=ids['item'], installment_number=1,
                      installment_total=3, period_label='01/2026')
        p2 = _billing(conn, ids, billing_type='item', item_id=ids['item'], installment_number=1,
                      installment_total=3, period_label='02/2026')
        zero = _billing(conn, ids, billing_type='avulsa', amount=0, period_label='03/2026')

    with pytest.raises(RuntimeError) as exc:
        _alembic(banco, 'upgrade', 'head')
    mensagem = str(exc.value)
    assert 'mensalidade_duplicada' in mensagem and f'{a}' in mensagem and f'{b}' in mensagem
    assert 'parcela_servico_duplicada' in mensagem and f'{p1}' in mensagem and f'{p2}' in mensagem
    assert 'cobranca_valor_invalido' in mensagem and f'id={zero}' in mensagem

    # Transação inteira desfeita: revisão, colunas, função e dados intactos.
    assert _revisao(banco) == ANTERIOR
    assert 'competencia' not in _colunas(banco, 'billings')
    with banco.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM pg_proc WHERE proname = 'mastersat_competencia'")).scalar_one() == 0
        assert conn.execute(text('SELECT count(*) FROM billings')).scalar_one() == 5

    # Saneamento documentado: a paga fica; a pendente duplicada é removida
    # (soft delete); a parcela repetida é cancelada; o valor zero é corrigido.
    with banco.begin() as conn:
        conn.execute(text('UPDATE billings SET is_deleted = true WHERE id = :id'), {'id': a})
        conn.execute(text("UPDATE billings SET status = 'CANCELED' WHERE id = :id"), {'id': p2})
        conn.execute(text('UPDATE billings SET amount = 0.01 WHERE id = :id'), {'id': zero})
    _alembic(banco, 'upgrade', 'head')
    assert _revisao(banco) == FASE02


def test_script_de_preflight_nao_altera_o_banco(banco):
    _alembic(banco, 'upgrade', ANTERIOR)
    with banco.begin() as conn:
        ids = _seed_base(conn)
        a = _billing(conn, ids, period_label='09/2026')
        b = _billing(conn, ids, period_label='9/2026')

    spec = importlib.util.spec_from_file_location('preflight_fase02', BACKEND / 'scripts' / 'preflight_fase02.py')
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    violacoes = modulo.executar(banco)

    duplicadas = next(v for v in violacoes if v['codigo'] == 'mensalidade_duplicada')
    assert duplicadas['total'] == 1 and duplicadas['linhas'][0]['ids'] == [a, b]
    with banco.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM pg_proc WHERE proname LIKE 'mastersat_%'")).scalar_one() == 0
    assert 'competencia' not in _colunas(banco, 'billings')
    assert _revisao(banco) == ANTERIOR


def test_downgrade_aborta_se_competencia_liberada_foi_recobrada(banco):
    _alembic(banco, 'upgrade', 'head')
    with banco.begin() as conn:
        ids = _seed_base(conn)
        cancelada = _billing(conn, ids, period_label='09/2026', status='CANCELED')
        conn.execute(text('UPDATE billings SET competencia_liberada = true WHERE id = :id'), {'id': cancelada})
        nova = _billing(conn, ids, period_label='09/2026')
    with pytest.raises(RuntimeError) as exc:
        _alembic(banco, 'downgrade', ANTERIOR)
    assert f'{cancelada}' in str(exc.value) and f'{nova}' in str(exc.value)
    assert _revisao(banco) == FASE02

    with banco.begin() as conn:
        conn.execute(text('UPDATE billings SET is_deleted = true WHERE id = :id'), {'id': cancelada})
    _alembic(banco, 'downgrade', ANTERIOR)
    assert 'competencia' not in _colunas(banco, 'billings')
    _alembic(banco, 'upgrade', 'head')
    assert _revisao(banco) == FASE02


def test_parser_python_e_sql_concordam(banco):
    _alembic(banco, 'upgrade', 'head')
    rotulos = [
        '09/2026', '9/2026', ' 9 / 2026 ', '9-2026', '09.2026', '2026-09', '2026/9', '2026 • T3',
        '2026•t1', '2026 • S2', '2026 • s1', '2026', '13/2026', '00/2026', '2026-13', '2026 • T5',
        '1899', '2200', 'Setembro/2026', '', '  ', '09/26', '092026', '2026-09-01', '٩/2026',
        '2026 - T3', '12/2199', '01/1900',
    ]
    with banco.connect() as conn:
        for rotulo in rotulos:
            sql = conn.execute(text('SELECT mastersat_competencia(:r)'), {'r': rotulo}).scalar_one()
            assert sql == competencia_do_rotulo(rotulo), rotulo


# ---------------------------------------------------------------------------
# DB-03 — carimbo de banco pré-Alembic
# ---------------------------------------------------------------------------

def _como_pre_alembic(engine, revisao: str) -> None:
    _alembic(engine, 'upgrade', revisao)
    with engine.begin() as conn:
        conn.execute(text('DROP TABLE alembic_version'))


def _diagnostico(engine):
    with engine.connect() as conn:
        return diagnosticar_schema_legado(conn)


def test_legado_completo_e_carimbado_na_revisao_certa(banco):
    _como_pre_alembic(banco, BASELINE)
    assert _diagnostico(banco).revisao == BASELINE


def test_legado_com_refresh_tokens_vai_para_a_revisao_seguinte(banco):
    _como_pre_alembic(banco, BASELINE_COM_REFRESH)
    assert _diagnostico(banco).revisao == BASELINE_COM_REFRESH


def test_legado_parcial_nao_e_carimbado(banco):
    _como_pre_alembic(banco, BASELINE)
    with banco.begin() as conn:
        conn.execute(text('ALTER TABLE contracts DROP COLUMN signed'))
        conn.execute(text('DROP INDEX ix_billings_receipt_number'))
    diag = _diagnostico(banco)
    assert diag.revisao is None
    assert 'contracts.signed' in diag.faltando
    assert 'índice ix_billings_receipt_number' in diag.faltando
    assert 'NÃO carimbou' in diag.relatorio()


def test_legado_com_objeto_de_migration_posterior_nao_e_carimbado(banco):
    _como_pre_alembic(banco, BASELINE)
    with banco.begin() as conn:
        conn.execute(text('ALTER TABLE billings ADD COLUMN sgr_payload json'))
    diag = _diagnostico(banco)
    assert diag.revisao is None
    assert 'billings.sgr_payload' in diag.posteriores


def test_boot_recusa_legado_parcial_sem_carimbar(banco, monkeypatch):
    import app.main as main

    _como_pre_alembic(banco, BASELINE)
    with banco.begin() as conn:
        conn.execute(text('ALTER TABLE billings DROP COLUMN period_label'))
    monkeypatch.setattr(main, 'engine', banco)
    chamadas = []
    monkeypatch.setattr('alembic.command.stamp', lambda *a, **k: chamadas.append(('stamp', a)))
    monkeypatch.setattr('alembic.command.upgrade', lambda *a, **k: chamadas.append(('upgrade', a)))
    with pytest.raises(RuntimeError) as exc:
        main._apply_database_migrations()
    assert 'billings.period_label' in str(exc.value)
    assert chamadas == []
