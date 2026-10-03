"""Exporta a preferência de NFS-e dos clientes ativos/inadimplentes do SGR.

Somente GET /buscar_cliente. Não altera o SGR nem o banco MasterSat.

Uso (na pasta backend):
    python scripts/exportar_preferencia_nf_sgr.py
    python scripts/exportar_preferencia_nf_sgr.py --output scripts/sgr_saida/preferencias.csv

O CSV só substitui o destino depois de TODAS as páginas terem sido lidas.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)  # carrega backend/.env sem depender da pasta de chamada

from app.services.sgr_migration.client import (  # noqa: E402
    ColetaPaginada, SGRApiError, SGRClient, SGRError, iterar_paginas,
)
from app.services.sgr_migration.mapping import ci_get  # noqa: E402
from app.services.sgr_migration.normalize import only_digits  # noqa: E402

DEFAULT_OUTPUT = BACKEND_DIR / 'scripts' / 'sgr_saida' / 'clientes_nota_fiscal_ativos_inadimplentes.csv'
SITUACOES = {'ATIVO', 'INADIMPLENTE'}
CAMPOS = ('codigo_sgr', 'nome', 'cpf_cnpj', 'situacao', 'nota_fiscal_sgr', 'emitir_nota_fiscal')


def linha_cliente(raw: dict) -> dict | None:
    situacao = str(ci_get(ci_get(raw, 'situacao'), 'descricao') or '').strip().upper()
    if situacao not in SITUACOES:
        return None
    flag = str(ci_get(raw, 'nota_fiscal_cliente') or '').strip().upper()
    return {
        'codigo_sgr': str(ci_get(raw, 'cod_cliente') or '').strip(),
        'nome': str(ci_get(raw, 'nome_cliente') or '').strip(),
        'cpf_cnpj': only_digits(str(ci_get(raw, 'cpf_cliente') or '')),
        'situacao': situacao,
        'nota_fiscal_sgr': flag,
        'emitir_nota_fiscal': {'S': 'sim', 'N': 'nao'}.get(flag, 'nao_informado'),
    }


def consultar_todos(client: SGRClient) -> tuple[list[dict], ColetaPaginada]:
    coleta = ColetaPaginada('/buscar_cliente', 'todos')
    linhas: list[dict] = []
    codigos: set[str] = set()
    for pagina in iterar_paginas(
        lambda total, indice: client.buscar_clientes(total=total, indice=indice),
        200, coleta,
    ):
        for raw in pagina:
            linha = linha_cliente(raw)
            if linha is None:
                continue
            codigo = linha['codigo_sgr']
            if not codigo or codigo in codigos:
                raise ValueError('Código SGR ausente ou duplicado; CSV não foi gerado.')
            codigos.add(codigo)
            linhas.append(linha)
    if not coleta.completa:
        raise RuntimeError('Paginação do SGR incompleta; CSV não foi gerado.')
    linhas.sort(key=lambda item: (item['nome'].casefold(), item['codigo_sgr']))
    return linhas, coleta


def gravar_csv(linhas: list[dict], destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8-sig', newline='',
            dir=destino.parent, prefix=destino.stem + '.', suffix='.tmp', delete=False,
        ) as arquivo:
            temporario = Path(arquivo.name)
            writer = csv.DictWriter(arquivo, fieldnames=CAMPOS, delimiter=';')
            writer.writeheader()
            writer.writerows(linhas)
        os.replace(temporario, destino)
    finally:
        if temporario is not None:
            temporario.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        linhas, coleta = consultar_todos(SGRClient())
        gravar_csv(linhas, args.output)
    except (SGRError, SGRApiError, RuntimeError, ValueError, OSError) as exc:
        print(f'Falha na consulta ao SGR: {exc}', file=sys.stderr)
        return 1
    resumo = Counter(linha['emitir_nota_fiscal'] for linha in linhas)
    print(f'Arquivo: {args.output.resolve()}')
    print(f'Clientes: {len(linhas)}; páginas: {coleta.paginas}; sim: {resumo["sim"]}; '
          f'não: {resumo["nao"]}; não informado: {resumo["nao_informado"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
