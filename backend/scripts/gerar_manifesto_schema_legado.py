"""Gera app/db/manifesto_schema_legado.json a partir das próprias migrations.

O boot só carimba um banco pré-Alembic numa revisão se o schema dele contiver
tudo o que essa revisão cria (ver app/db/legacy_schema.py). O "tudo" vem
deste manifesto: tabelas, colunas e índices de um PostgreSQL vazio migrado até
cada revisão legada admitida.

Rodar SOMENTE contra um PostgreSQL descartável — o script cria e apaga um
banco temporário no servidor apontado:

    TEST_DATABASE_URL=postgresql+psycopg://... python scripts/gerar_manifesto_schema_legado.py

Só precisa ser rodado de novo se uma das revisões abaixo mudar (não deve: são
histórico aplicado).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.legacy_schema import MANIFESTO, REVISOES_LEGADAS  # noqa: E402


def _config(connection):
    from alembic.config import Config

    cfg = Config(str(BACKEND / 'alembic.ini'))
    cfg.set_main_option('script_location', str(BACKEND / 'alembic'))
    cfg.attributes['connection'] = connection
    return cfg


def inventario(connection) -> dict[str, dict[str, list[str]]]:
    insp = inspect(connection)
    tabelas = {}
    for tabela in sorted(insp.get_table_names()):
        if tabela == 'alembic_version':
            continue
        tabelas[tabela] = {
            'colunas': sorted(col['name'] for col in insp.get_columns(tabela)),
            'indices': sorted(idx['name'] for idx in insp.get_indexes(tabela)),
        }
    return tabelas


def main() -> int:
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        print('Defina TEST_DATABASE_URL (PostgreSQL descartável).', file=sys.stderr)
        return 2
    from alembic import command

    base = make_url(url)
    nome = f'manifesto_{uuid4().hex[:12]}'
    admin = create_engine(base, isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{nome}"'))
    engine = create_engine(base.set(database=nome))
    manifesto = {}
    try:
        for revisao in REVISOES_LEGADAS:
            with engine.begin() as conn:
                command.upgrade(_config(conn), revisao)
            with engine.connect() as conn:
                manifesto[revisao] = inventario(conn)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{nome}"'))
        admin.dispose()

    MANIFESTO.write_text(json.dumps(manifesto, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'{MANIFESTO} gravado: ' + ', '.join(f'{r}={len(t)} tabelas' for r, t in manifesto.items()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
