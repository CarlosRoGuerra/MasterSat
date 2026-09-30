"""scripts/saneamento_parcela_sgr.py contra PostgreSQL real, no schema da
Fase 01 (é onde o banco legado está quando o preflight da Fase 02 recusa)."""
from __future__ import annotations

import importlib.util
import json

import pytest
from sqlalchemy import text

from tests.test_fase02_migrations_postgres import (  # noqa: F401 — fixture e helpers
    ANTERIOR, BACKEND, FASE02, _alembic, _billing, _revisao, _seed_base, banco,
)

pytestmark = pytest.mark.postgres


def _script():
    spec = importlib.util.spec_from_file_location('saneamento', BACKEND / 'scripts' / 'saneamento_parcela_sgr.py')
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_saneamento_destrava_a_migration_sem_mexer_em_valor(banco):
    _alembic(banco, 'upgrade', ANTERIOR)
    sgr = json.dumps({'parcela': '2 de 1'})
    with banco.begin() as conn:
        ids = _seed_base(conn)
        ruins = [
            _billing(conn, ids, billing_type='avulsa', installment_number=0, installment_total=1, sgr_payload=sgr),
            _billing(conn, ids, billing_type='avulsa', installment_number=2, installment_total=1, sgr_payload=sgr),
            _billing(conn, ids, billing_type='avulsa', installment_number=0, installment_total=0, sgr_payload=sgr),
        ]
        boa = _billing(conn, ids, billing_type='avulsa', installment_number=1, installment_total=3, sgr_payload=sgr)
        manual = _billing(conn, ids, billing_type='avulsa', installment_number=5, installment_total=2)
    with pytest.raises(RuntimeError):
        _alembic(banco, 'upgrade', FASE02)

    script = _script()
    with banco.begin() as conn:
        r = script.resumo(conn)
        assert r['sgr'] == 3 and r['nao_sgr'] == [manual]
        assert script.aplicar(conn) == 3
    with banco.connect() as conn:
        linhas = dict(conn.execute(text(
            'SELECT id, installment_number FROM billings WHERE id = ANY(:ids)'), {'ids': ruins + [boa]}).all())
        logs = conn.execute(text("SELECT count(*) FROM billing_change_logs WHERE field_name = 'parcela'")).scalar()
        payload = conn.execute(text('SELECT sgr_payload FROM billings WHERE id = :i'), {'i': ruins[1]}).scalar()
        valores = conn.execute(text('SELECT count(DISTINCT amount) FROM billings')).scalar()
    assert [linhas[i] for i in ruins] == [None, None, None] and linhas[boa] == 1
    assert logs == 3 and '2 de 1' in str(payload) and valores == 1

    # A não-SGR continua bloqueando (revisão manual); corrigida, a migration sobe.
    with banco.begin() as conn:
        conn.execute(text('UPDATE billings SET installment_number = 2 WHERE id = :i'), {'i': manual})
    _alembic(banco, 'upgrade', FASE02)
    assert _revisao(banco) == FASE02
