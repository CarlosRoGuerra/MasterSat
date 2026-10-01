"""Preflight / inventário da migration b7d3e1f5a902 (Fase 03) — só leitura.

Nada bloqueia a migration (ela é aditiva). O relatório lista, com IDs, o que
ela vai marcar e o que fica para revisão humana:

* ``titulo_ativo_sem_obrigacao`` — título registrado no banco com a cobrança
  cancelada, removida ou recebida fora do boleto (vai para baixa pendente);
* ``erro_registro_ambiguo`` — ERRO_REGISTRO cuja última tentativa não teve
  resposta conclusiva (vira DESFECHO_DESCONHECIDO; a conciliação consulta);
* ``erro_registro_sem_evidencia``, ``reserva_orfa``,
  ``paga_com_valor_divergente``, ``conta_a_pagar_inconsistente`` — revisão;
* ``carteira_monitorada`` — tamanho da carteira que a conciliação percorre.

Não envia cobrança, não dá baixa, não chama a Ailos. Transação sempre
termina em ROLLBACK. Rode num snapshot restaurado sempre que possível:

    python scripts/preflight_fase03.py                  # usa DATABASE_URL
    python scripts/preflight_fase03.py --json saida.json
    python scripts/preflight_fase03.py --depois          # banco já migrado

Runbook: docs/financeiro/runbook-desfecho-desconhecido.md.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine

BACKEND = Path(__file__).resolve().parent.parent
MIGRATION = BACKEND / 'alembic' / 'versions' / 'b7d3e1f5a902_fase03_emissao_conciliacao.py'


def _migration():
    spec = importlib.util.spec_from_file_location('fase03_migration', MIGRATION)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def executar(engine, *, depois_da_migration: bool = False) -> list[dict]:
    migration = _migration()
    with engine.connect() as conn:
        transacao = conn.begin()
        try:
            return migration.coletar_inventario(conn, depois_da_migration=depois_da_migration)
        finally:
            transacao.rollback()


def _json_padrao(valor):
    if isinstance(valor, (date, datetime, Decimal)):
        return str(valor)
    raise TypeError(type(valor))


def formatar(itens: list[dict], limite: int = 30) -> str:
    partes = []
    for item in itens:
        acao = f" → migration: {item['acao_migration']}" if item['acao_migration'] else ' → revisão'
        partes.append(f"[{item['codigo']}] {item['total']} — {item['descricao']}{acao}")
        if item['codigo'] == 'carteira_monitorada':
            continue
        for linha in item['linhas'][:limite]:
            partes.append('    ' + ', '.join(f'{k}={v}' for k, v in linha.items()))
        if item['total'] > limite:
            partes.append(f'    … mais {item["total"] - limite}')
    return '\n'.join(partes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--json', help='grava o inventário completo neste arquivo')
    parser.add_argument('--depois', action='store_true',
                        help='banco já migrado: ignora títulos que já têm baixa registrada')
    args = parser.parse_args()

    sys.path.insert(0, str(BACKEND))
    from app.core.config import settings

    engine = create_engine(settings.database_url)
    try:
        itens = executar(engine, depois_da_migration=args.depois)
    finally:
        engine.dispose()
    print(formatar(itens))
    if args.json:
        Path(args.json).write_text(
            json.dumps(itens, indent=1, ensure_ascii=False, default=_json_padrao), encoding='utf-8',
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
