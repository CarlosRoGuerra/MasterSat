"""
Compara os vínculos placa ↔ rastreador do SGR (sistema antigo) com os do
MasterSat e lista as placas que divergem.

SOMENTE LEITURA nos dois lados:
  - SGR: só /buscar_rastreador (paginado até o fim, mesma leitura da
    importação) — traz IMEI, placa e situação de todos os equipamentos;
  - MasterSat: SELECT numa transação READ ONLY — se algo
    tentasse gravar, o PostgreSQL recusaria.

Saída: resumo no terminal + CSV (separado por ";", abre no Excel) com uma
linha por placa divergente. Detalhes dos tipos em
app/services/sgr_migration/comparar_vinculos.py.

Uso (no servidor, dentro do backend — que já tem banco e SGR_* no .env):
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend \\
      python scripts/sgr_comparar_vinculos.py --csv /tmp/vinculos.csv
  docker compose -f docker-compose.yml -f docker-compose.prod.yml cp backend:/tmp/vinculos.csv .
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

_BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, _BACKEND_DIR)
os.chdir(_BACKEND_DIR)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass

_SQL_VEICULOS = """
    SELECT v.plate AS placa, cl.name AS cliente
    FROM vehicles v LEFT JOIN clients cl ON cl.id = v.client_id
    WHERE NOT v.is_deleted
"""
_SQL_INSTALADOS = """
    SELECT v.plate AS placa, t.imei
    FROM trackers t JOIN vehicles v ON v.id = t.vehicle_id
    WHERE NOT t.is_deleted AND NOT v.is_deleted
"""
_SQL_CONTRATOS = """
    SELECT v.plate AS placa, c.id AS contrato_id, t.imei
    FROM contracts c
    JOIN vehicles v ON v.id = c.vehicle_id
    LEFT JOIN trackers t ON t.id = c.tracker_id
    WHERE c.status = 'ativo' AND NOT c.is_deleted AND NOT v.is_deleted
"""


def conexao_somente_leitura(engine):
    """Conexão cuja transação é READ ONLY (PostgreSQL): qualquer INSERT/UPDATE/
    DELETE nela é recusado pelo próprio banco. Vale só para esta transação —
    a conexão volta ao pool sem herdar a restrição."""
    conn = engine.connect()
    if conn.dialect.name == 'postgresql':
        conn.exec_driver_sql('SET TRANSACTION READ ONLY')
    return conn


def ler_mastersat(database_url: str | None = None, *, engine=None) -> tuple[list[dict], list[dict], list[dict]]:
    from sqlalchemy import create_engine, text

    proprio = engine is None
    engine = engine or create_engine(database_url)
    try:
        with conexao_somente_leitura(engine) as conn:
            veiculos = [dict(r._mapping) for r in conn.execute(text(_SQL_VEICULOS))]
            instalados = [dict(r._mapping) for r in conn.execute(text(_SQL_INSTALADOS))]
            contratos = [dict(r._mapping) for r in conn.execute(text(_SQL_CONTRATOS))]
            conn.rollback()
    finally:
        if proprio:
            engine.dispose()
    return veiculos, instalados, contratos


def ler_sgr() -> list[dict]:
    from app.services.sgr_migration.client import ColetaPaginada, SGRClient, iterar_paginas
    from app.services.sgr_migration.mapping import ci_get
    from app.services.sgr_migration.poc import _PAGINA_RASTREADOR, _com_tentativas

    sgr = SGRClient()
    coleta = ColetaPaginada('/buscar_rastreador', 'todos')
    buscar = _com_tentativas(lambda total, indice: sgr.buscar_rastreadores(total=total, indice=indice))
    linhas: list[dict] = []
    for lote in iterar_paginas(buscar, _PAGINA_RASTREADOR, coleta):
        for rast in lote:
            linhas.append({
                'placa': ci_get(rast, 'placa'),
                'imei': ci_get(rast, 'imei_equipamento'),
                'situacao': ci_get(rast, 'situacao'),
            })
    if not coleta.completa:
        raise RuntimeError('Leitura do SGR incompleta — comparação abortada para não apontar falsas divergências.')
    return linhas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--csv', help='arquivo de saída (padrão: scripts/sgr_saida/comparacao_vinculos_<data>.csv)')
    args = parser.parse_args(argv)

    from app.core.config import settings
    from app.services.sgr_migration.comparar_vinculos import TIPOS, comparar, resumo

    print('Lendo SGR (/buscar_rastreador, somente leitura)...')
    sgr = ler_sgr()
    print(f'  {len(sgr)} rastreador(es) no SGR')
    print('Lendo MasterSat (transação READ ONLY)...')
    veiculos, instalados, contratos = ler_mastersat(settings.database_url)
    print(f'  {len(veiculos)} veículo(s), {len(instalados)} rastreador(es) instalado(s), {len(contratos)} contrato(s) ativo(s)')

    divergencias = comparar(sgr, veiculos, instalados, contratos)
    print(f'\n{len(divergencias)} placa(s) com vínculo divergente:')
    for tipo, qtd in resumo(divergencias).items():
        print(f'  {qtd:5d}  {TIPOS[tipo]}')

    destino = Path(args.csv) if args.csv else (
        Path(_BACKEND_DIR) / 'scripts' / 'sgr_saida' / f'comparacao_vinculos_{datetime.now():%Y%m%d_%H%M}.csv'
    )
    destino.parent.mkdir(parents=True, exist_ok=True)
    linhas = [d.linha() for d in divergencias]
    with destino.open('w', encoding='utf-8-sig', newline='') as fh:
        campos = list(linhas[0]) if linhas else ['placa']
        escritor = csv.DictWriter(fh, fieldnames=campos, delimiter=';')
        escritor.writeheader()
        escritor.writerows(linhas)
    print(f'\nRelatório: {destino}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
