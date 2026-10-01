"""Fase 03 — migration b7d3e1f5a902 contra PostgreSQL real.

Inventário (baixa pendente, desfecho desconhecido), preflight sem efeito
colateral e rollback que preserva estado e eventos: downgrade → código
antigo → upgrade devolve tudo.
"""
from __future__ import annotations

import importlib.util
from datetime import date

import pytest
from sqlalchemy import text

from tests.test_fase02_migrations_postgres import (  # noqa: F401 — fixture e helpers
    BACKEND,
    FASE02,
    _alembic,
    _billing,
    _colunas,
    _revisao,
    _seed_base,
    banco,
)

pytestmark = pytest.mark.postgres
FASE03 = 'b7d3e1f5a902'


def _boleto(conn, billing_id: int, *, registrado=True, status='0') -> None:
    conn.execute(text(
        "INSERT INTO ailos_boletos (billing_id, numero_convenio, nosso_numero, linha_digitavel, codigo_barras, status_ailos) "
        "VALUES (:b, '102004', :nn, :ld, :cb, :st)"
    ), {'b': billing_id, 'nn': f'NN{billing_id}' if registrado else None,
        'ld': f'LD{billing_id}' if registrado else None, 'cb': f'CB{billing_id}' if registrado else None,
        'st': status})


def _log(conn, billing_id: int, status_code: int | None) -> None:
    conn.execute(text(
        "INSERT INTO ailos_api_logs (endpoint, method, status_code, success, error_message, billing_id) "
        "VALUES ('/v2/boletos/gerar/boleto/convenios/102004', 'POST', :sc, false, 'x', :b)"
    ), {'b': billing_id, 'sc': status_code})


def _legado(banco) -> dict:
    """Dados como a Fase 02 deixaria (SQL puro: o ORM atual tem colunas novas)."""
    with banco.begin() as conn:
        ids = _seed_base(conn)
        casos = {
            'removida': _billing(conn, ids, billing_type='avulsa', is_deleted=True),
            'cancelada': _billing(conn, ids, billing_type='avulsa', status='CANCELED'),
            'paga_pix': _billing(conn, ids, billing_type='avulsa', status='PAID', paid_amount=50,
                                 payment_method='pix', payment_date=date(2026, 9, 1)),
            'paga_boleto': _billing(conn, ids, billing_type='avulsa', status='PAID', paid_amount=50,
                                    payment_method='boleto', payment_date=date(2026, 9, 1)),
            'aberta': _billing(conn, ids, billing_type='avulsa'),
            'timeout': _billing(conn, ids, billing_type='avulsa'),
            'recusada': _billing(conn, ids, billing_type='avulsa'),
        }
        for nome in ('removida', 'cancelada', 'paga_pix', 'paga_boleto', 'aberta'):
            _boleto(conn, casos[nome])
        _boleto(conn, casos['timeout'], registrado=False, status='ERRO_REGISTRO')
        _log(conn, casos['timeout'], None)
        _boleto(conn, casos['recusada'], registrado=False, status='ERRO_REGISTRO')
        _log(conn, casos['recusada'], 422)
    return casos


def _estado(banco, billing_id: int) -> tuple:
    with banco.connect() as conn:
        return tuple(conn.execute(text(
            'SELECT status_ailos, baixa_status FROM ailos_boletos WHERE billing_id = :b'
        ), {'b': billing_id}).one())


def _preflight():
    spec = importlib.util.spec_from_file_location('preflight_fase03', BACKEND / 'scripts' / 'preflight_fase03.py')
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_preflight_lista_e_nao_altera_nada(banco):
    _alembic(banco, 'upgrade', FASE02)
    casos = _legado(banco)
    with banco.connect() as conn:
        antes = conn.execute(text('SELECT count(*), sum(length(coalesce(status_ailos, \'\'))) FROM ailos_boletos')).one()
    itens = {i['codigo']: i for i in _preflight().executar(banco)}
    ativos = sorted(linha['billing_id'] for linha in itens['titulo_ativo_sem_obrigacao']['linhas'])
    assert ativos == sorted([casos['removida'], casos['cancelada'], casos['paga_pix']])
    assert [linha['billing_id'] for linha in itens['erro_registro_ambiguo']['linhas']] == [casos['timeout']]
    with banco.connect() as conn:
        assert conn.execute(text('SELECT count(*), sum(length(coalesce(status_ailos, \'\'))) FROM ailos_boletos')).one() == antes
    assert _revisao(banco) == FASE02


def test_upgrade_marca_inventario_sem_tocar_valores(banco):
    _alembic(banco, 'upgrade', FASE02)
    casos = _legado(banco)
    with banco.connect() as conn:
        valores_antes = conn.execute(text('SELECT id, amount, status, is_deleted FROM billings ORDER BY id')).all()
    _alembic(banco, 'upgrade', FASE03)

    for nome in ('removida', 'cancelada', 'paga_pix'):
        assert _estado(banco, casos[nome])[1] == 'pendente', nome
    for nome in ('paga_boleto', 'aberta'):
        assert _estado(banco, casos[nome])[1] is None, nome
    assert _estado(banco, casos['timeout'])[0] == 'DESFECHO_DESCONHECIDO'
    assert _estado(banco, casos['recusada'])[0] == 'ERRO_REGISTRO'
    with banco.connect() as conn:
        assert conn.execute(text('SELECT id, amount, status, is_deleted FROM billings ORDER BY id')).all() == valores_antes
        log = conn.execute(text(
            "SELECT justification FROM billing_change_logs WHERE billing_id = :b AND field_name = 'registro_ailos'"
        ), {'b': casos['timeout']}).scalar_one()
    assert 'Fase 03' in log


def test_rollback_preserva_estado_e_eventos_e_volta(banco):
    _alembic(banco, 'upgrade', FASE02)
    casos = _legado(banco)
    _alembic(banco, 'upgrade', FASE03)
    with banco.begin() as conn:
        conn.execute(text(
            "INSERT INTO billing_adjustments (billing_id, kind, amount, justification) "
            "VALUES (:b, 'desconto', 5, 'acordo')"
        ), {'b': casos['paga_pix']})
        conn.execute(text(
            "UPDATE ailos_boletos SET pendencia = 'pagamento_divergente', ultima_consulta_em = now() "
            "WHERE billing_id = :b"
        ), {'b': casos['aberta']})

    _alembic(banco, 'downgrade', FASE02)
    assert _revisao(banco) == FASE02
    assert 'baixa_status' not in _colunas(banco, 'ailos_boletos')
    with banco.connect() as conn:
        # O código antigo trata PROCESSANDO como "em registro" (bloqueia edição).
        assert conn.execute(text('SELECT status_ailos FROM ailos_boletos WHERE billing_id = :b'),
                            {'b': casos['timeout']}).scalar_one() == 'PROCESSANDO'
        assert conn.execute(text('SELECT count(*) FROM fase03_preservado_billing_adjustments')).scalar_one() == 1
        assert conn.execute(text('SELECT count(*) FROM fase03_preservado_ailos_boletos')).scalar_one() >= 4
        tabelas = set(conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )).scalars())
    assert 'payable_change_logs' not in tabelas  # vazia: removida

    # Código antigo operando: cancela a aberta (que tem título) por fora.
    with banco.begin() as conn:
        conn.execute(text("UPDATE billings SET status = 'CANCELED' WHERE id = :b"), {'b': casos['aberta']})

    _alembic(banco, 'upgrade', FASE03)
    assert _estado(banco, casos['timeout'])[0] == 'DESFECHO_DESCONHECIDO'
    assert _estado(banco, casos['cancelada'])[1] == 'pendente'
    # A cancelada pelo código antigo entra no inventário de novo.
    assert _estado(banco, casos['aberta'])[1] == 'pendente'
    with banco.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM billing_adjustments')).scalar_one() == 1
        assert conn.execute(text('SELECT pendencia FROM ailos_boletos WHERE billing_id = :b'),
                            {'b': casos['aberta']}).scalar_one() == 'pagamento_divergente'
        preservadas = conn.execute(text(
            "SELECT count(*) FROM pg_tables WHERE tablename LIKE 'fase03_preservado_%'"
        )).scalar_one()
    assert preservadas == 0


def test_desfecho_resolvido_pelo_codigo_antigo_nao_volta(banco):
    _alembic(banco, 'upgrade', FASE02)
    casos = _legado(banco)
    _alembic(banco, 'upgrade', FASE03)
    _alembic(banco, 'downgrade', FASE02)
    with banco.begin() as conn:  # código antigo registrou o título no retry
        conn.execute(text(
            "UPDATE ailos_boletos SET status_ailos = '0', linha_digitavel = 'LDX', codigo_barras = 'CBX' "
            "WHERE billing_id = :b"
        ), {'b': casos['timeout']})
    _alembic(banco, 'upgrade', FASE03)
    assert _estado(banco, casos['timeout'])[0] == '0'
