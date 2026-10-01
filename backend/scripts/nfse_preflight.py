"""Inventário somente leitura antes/depois da Fase 05, sem XML nem segredos.

Uso: python scripts/nfse_preflight.py (DATABASE_URL de cópia autorizada).
Não consulta o fisco, não corrige dados e não inicia workers.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text
from app.db.session import engine


def inventario(conn):
    columns = {c['name'] for c in inspect(conn).get_columns('nfse_notas')}
    modern = 'tentativa_id' in columns
    result = {
        'schema_fase05': modern,
        'por_status': dict(conn.execute(text(
            'SELECT status, count(*) FROM nfse_notas GROUP BY status ORDER BY status'
        )).all()),
        'notas_com_xml_envio': conn.scalar(text(
            'SELECT count(*) FROM nfse_notas WHERE xml_envio IS NOT NULL')),
        'notas_com_xml_retorno': conn.scalar(text(
            'SELECT count(*) FROM nfse_notas WHERE xml_retorno IS NOT NULL')),
    }
    filtro = "status IN ('pending', 'processing', 'erro')"
    if modern:
        filtro += ' AND tentativa_id IS NULL AND erro_tipo IS NULL'
    result['legado_a_reconciliar_ids'] = list(conn.scalars(text(
        f'SELECT id FROM nfse_notas WHERE {filtro} ORDER BY id')))
    if modern:
        result['desconhecidas_ids'] = list(conn.scalars(text(
            "SELECT id FROM nfse_notas WHERE status = 'desconhecido' ORDER BY id")))
        result['sem_identidade_consulta_ids'] = list(conn.scalars(text(
            "SELECT id FROM nfse_notas WHERE status = 'desconhecido' "
            "AND (provedor IS NULL OR ambiente IS NULL) ORDER BY id")))
    return result


if __name__ == '__main__':
    with engine.connect() as conn:
        print(json.dumps(inventario(conn), ensure_ascii=False, indent=2))
