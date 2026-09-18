"""
POC de migração SGR (Hinova) → MasterSat — SOMENTE LEITURA.

O que este script faz:
  1. `check`  — ETAPA 5: autentica na API do SGR e faz 1 consulta mínima
      (1 cliente) para validar a conectividade/credenciais.
  2. `run`    — ETAPAS 6/7/8: busca no máximo SGR_MIGRATION_LIMIT clientes
      (padrão 10), descobre os veículos de cada um e os equipamentos/
      rastreadores de cada veículo, mapeia tudo para o formato do MasterSat
      e imprime um relatório de compatibilidade. NÃO grava nada no banco do
      MasterSat — o resultado só existe em memória e, opcionalmente, num
      arquivo JSON de diagnóstico local (nunca versionado, ver .gitignore).

Este script NUNCA executa POST/PUT/PATCH/DELETE contra o SGR — usa
exclusivamente app.services.sgr_migration.client.SGRClient, que só expõe
métodos de consulta (GET).

Credenciais: exclusivamente via variáveis de ambiente (.env) — ver
SGR_BASE_URL/SGR_COD_MOBILE/SGR_USERNAME/SGR_PASSWORD/SGR_API_KEY em
.env.example. Nunca são impressas neste script, nem mascaradas: são
totalmente omitidas de qualquer log/print.

Uso:
  cd backend
  python scripts/sgr_poc.py check
  python scripts/sgr_poc.py run --limit 10 --output scripts/sgr_saida/migration-test-result.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# O relatório usa 🟢/🟡/🔴 e acentuação; o console do Windows abre em cp1252 e
# estoura UnicodeEncodeError ao imprimir. errors='replace' garante que um
# terminal limitado degrade o caractere em vez de derrubar a execução.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):  # stream redirecionado/sem suporte
        pass

_BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
# Garante que 'app' é encontrado independente de onde o script for chamado.
sys.path.insert(0, _BACKEND_DIR)
# pydantic-settings resolve env_file='.env' (app/core/config.py) relativo ao
# diretório de trabalho ATUAL, não ao local do script — rodar de dentro de
# backend/scripts/ faria o .env de backend/ passar batido, sem nenhum erro
# (só credenciais "ausentes"). Força o cwd para a raiz do backend antes de
# importar as settings, assim funciona de qualquer diretório.
os.chdir(_BACKEND_DIR)

from app.core.config import settings  # noqa: E402
from app.services.sgr_migration.client import SGRClient, SGRApiError, SGRError  # noqa: E402
from app.services.sgr_migration.poc import check_connectivity, run_poc  # noqa: E402
from app.services.sgr_migration.report import build_report, render_text_report  # noqa: E402

DEFAULT_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sgr_saida', 'migration-test-result.json')


def _print_config_banner() -> None:
    def _status(value: str) -> str:
        return '***configurado***' if value else 'NÃO CONFIGURADO'

    print('Configuração SGR (valores nunca exibidos):')
    print(f'  SGR_BASE_URL: {settings.sgr_base_url}')
    print(f'  SGR_COD_MOBILE: {_status(settings.sgr_cod_mobile)}')
    print(f'  SGR_USERNAME: {_status(settings.sgr_username)}')
    print(f'  SGR_PASSWORD: {_status(settings.sgr_password)}')
    print(f'  SGR_API_KEY: {_status(settings.sgr_api_key)}')
    print()


def cmd_check(_args: argparse.Namespace) -> int:
    _print_config_banner()
    client = SGRClient()
    try:
        result = check_connectivity(client)
    except (SGRError, SGRApiError) as exc:
        print(f'FALHA na conectividade com o SGR: {exc}')
        return 1
    print('Conectividade com o SGR: OK')
    print(f'  Autenticado: {result["autenticado"]}')
    print(f'  Registros retornados na consulta de teste: {result["registros_retornados"]}')
    print(f'  Requisições realizadas: {result["requisicoes"]}')
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    limit = args.limit or settings.sgr_migration_limit
    _print_config_banner()
    print(f'Buscando no máximo {limit} clientes (READ-ONLY)...\n')

    client = SGRClient()
    try:
        result = run_poc(client, limit)
    except (SGRError, SGRApiError) as exc:
        print(f'FALHA ao executar a POC de migração: {exc}')
        return 1

    report = build_report(result)
    print(render_text_report(report))

    output_path = args.output or DEFAULT_OUTPUT
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, default=str)
    print(f'\nRelatório de diagnóstico salvo em: {output_path}')
    print('(arquivo local, fora do git — ver .gitignore)')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest='command', required=True)

    check_parser = subparsers.add_parser('check', help='ETAPA 5 — teste de conectividade/autenticação')
    check_parser.set_defaults(func=cmd_check)

    run_parser = subparsers.add_parser('run', help='ETAPAS 6-8 — POC completa (N clientes + relacionamentos)')
    run_parser.add_argument('--limit', type=int, default=None, help='Nº de clientes (padrão: SGR_MIGRATION_LIMIT, 10)')
    run_parser.add_argument('--output', type=str, default=None, help=f'Caminho do JSON de diagnóstico (padrão: {DEFAULT_OUTPUT})')
    run_parser.set_defaults(func=cmd_run)

    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
