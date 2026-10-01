"""Preflight da migration e5c2a9d71f04 (Fase 02) — só leitura.

Lista, com IDs e valores, tudo o que faria a migration abortar (duplicatas
de competência, parcelas repetidas, valores ≤ 0...) e o inventário para o
saneamento (rótulos fora de formato, originais consolidadas cujo substituto
sumiu, marcadores de substituição sem alvo).

Não altera dados nem schema: a função de competência é criada como cópia
temporária (pg_temp, some com a sessão) dentro de uma transação que termina
sempre em ROLLBACK. Rode num snapshot restaurado sempre que possível:

    python scripts/preflight_fase02.py                 # usa DATABASE_URL
    python scripts/preflight_fase02.py --json saida.json

Código de saída: 0 sem bloqueios, 1 com bloqueios (a migration abortaria).
Saneamento de cada código: docs/financeiro/saneamento-fase-02.md.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parent.parent
MIGRATION = BACKEND / 'alembic' / 'versions' / 'e5c2a9d71f04_fase02_obrigacao_competencia.py'
FUNCAO_TEMPORARIA = 'pg_temp.mastersat_competencia'


def _migration():
    spec = importlib.util.spec_from_file_location('fase02_migration', MIGRATION)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def executar(engine) -> list[dict]:
    migration = _migration()
    with engine.connect() as conn:
        transacao = conn.begin()
        try:
            conn.execute(text(migration.SQL_FUNCAO_COMPETENCIA.format(fn=FUNCAO_TEMPORARIA)))
            return migration.coletar_violacoes(conn, fn=FUNCAO_TEMPORARIA)
        finally:
            transacao.rollback()


def _json_padrao(valor):
    if isinstance(valor, (date, Decimal)):
        return str(valor)
    raise TypeError(type(valor))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--url', help='URL do banco (padrão: DATABASE_URL das settings)')
    parser.add_argument('--json', help='grava o relatório completo neste arquivo')
    args = parser.parse_args()

    url = args.url
    if not url:
        sys.path.insert(0, str(BACKEND))
        from app.core.config import settings
        url = settings.database_url
    engine = create_engine(url)
    try:
        violacoes = executar(engine)
    finally:
        engine.dispose()

    print(_migration().formatar_relatorio(violacoes, limite_por_item=200) or 'Nenhuma pendência encontrada.')
    bloqueantes = sum(v['total'] for v in violacoes if v['bloqueia'])
    print(f'\nBloqueios: {bloqueantes}. Inventário: '
          + ', '.join(f"{v['codigo']}={v['total']}" for v in violacoes if not v['bloqueia']))
    if args.json:
        Path(args.json).write_text(
            json.dumps(violacoes, indent=1, ensure_ascii=False, default=_json_padrao), encoding='utf-8',
        )
    return 1 if bloqueantes else 0


if __name__ == '__main__':
    raise SystemExit(main())
