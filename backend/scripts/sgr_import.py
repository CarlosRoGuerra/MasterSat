"""
Importa do SGR para o banco do MasterSat os N primeiros clientes com seus
veículos e rastreadores.

Leitura no SGR (só GET, mesmo cliente da POC) + ESCRITA no MasterSat.

Segurança:
  - dry-run é o PADRÃO: sem --apply nada é gravado (roda tudo e dá rollback,
    então já mostra erro de constraint que aconteceria de verdade);
  - --apply só é aceito se o banco for local; apontar para outro host exige
    --permitir-banco-remoto, para nunca escrever na produção por acidente;
  - reexecutar é seguro: dedup por CPF/CNPJ, placa e IMEI. Registro que já
    existe é reaproveitado, nunca sobrescrito.

Uso (a partir de backend/):
  python scripts/sgr_import.py                 # simulação
  python scripts/sgr_import.py --apply         # grava de verdade
  python scripts/sgr_import.py --limit 10 --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlsplit

_BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, _BACKEND_DIR)
os.chdir(_BACKEND_DIR)  # pydantic-settings acha o .env a partir do cwd

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass

# Fora do container, 'db' não resolve — o padrão aponta para o Postgres local
# publicado em 127.0.0.1:5432 (mesmo padrão dos outros scripts).
os.environ.setdefault('DATABASE_URL', 'postgresql+psycopg://postgres:postgres@localhost:5432/rastreamento')

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.sgr_migration.client import SGRApiError, SGRClient, SGRError  # noqa: E402
from app.services.sgr_migration.importer import import_poc_result  # noqa: E402
from app.services.sgr_migration.poc import run_poc  # noqa: E402

_HOSTS_LOCAIS = {'localhost', '127.0.0.1', '::1', 'db'}


def _banco_descricao() -> tuple[str, str]:
    """(host, descrição sem credencial) do banco alvo."""
    partes = urlsplit(settings.database_url)
    host = (partes.hostname or '').lower()
    return host, f'{host}:{partes.port or 5432}{partes.path}'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--limit', type=int, default=None, help='Nº de clientes (padrão: SGR_MIGRATION_LIMIT)')
    parser.add_argument('--apply', action='store_true', help='Grava de verdade (sem isto é só simulação)')
    parser.add_argument('--permitir-banco-remoto', action='store_true',
                        help='Libera --apply em banco não-local. Use com MUITA atenção.')
    parser.add_argument('--boletos', action='store_true',
                        help='Traz também o histórico de cobrança (1 chamada a mais por cliente)')
    args = parser.parse_args()

    limit = args.limit or settings.sgr_migration_limit
    host, banco = _banco_descricao()
    local = host in _HOSTS_LOCAIS

    print(f'Banco de destino: {banco}  ({"LOCAL" if local else "REMOTO"})')
    print(f'Origem: SGR Hinova · {limit} cliente(s)')
    print(f'Modo: {"GRAVANDO (--apply)" if args.apply else "SIMULAÇÃO (dry-run) — nada será gravado"}')
    print()

    if args.apply and not local and not args.permitir_banco_remoto:
        print('RECUSADO: --apply em banco NÃO-LOCAL sem --permitir-banco-remoto.')
        print('Se a intenção é mesmo escrever nesse banco, repita com a flag explícita.')
        return 1

    print('Lendo do SGR (somente leitura)...')
    sgr = SGRClient()
    try:
        resultado = run_poc(sgr, limit, com_boletos=args.boletos)
    except (SGRError, SGRApiError) as exc:
        print(f'FALHA ao ler o SGR: {exc}')
        return 1
    print(f'  {len(resultado.clients)} cliente(s) lidos em {resultado.request_count} requisição(ões).')
    print()

    db = SessionLocal()
    try:
        stats = import_poc_result(db, resultado, dry_run=not args.apply)
    except Exception as exc:  # noqa: BLE001 — qualquer erro precisa desfazer a transação
        db.rollback()
        print(f'FALHA ao importar: {type(exc).__name__}: {exc}')
        return 1
    finally:
        db.close()

    rotulo = 'criados' if args.apply else 'seriam criados'
    print(f'CLIENTES     {rotulo}: {stats.clients_created} | já existiam: {stats.clients_reused} | ignorados: {stats.clients_skipped}')
    print(f'VEÍCULOS     {rotulo}: {stats.vehicles_created} | já existiam: {stats.vehicles_reused} | ignorados: {stats.vehicles_skipped}')
    print(f'RASTREADORES {rotulo}: {stats.trackers_created} | já existiam: {stats.trackers_reused} | ignorados: {stats.trackers_skipped}')
    print(f'PLANOS       {rotulo}: {stats.plans_created} | já existiam: {stats.plans_reused} | ignorados: {stats.plans_skipped}')
    print(f'CONTRATOS    {rotulo}: {stats.contracts_created} | já existiam: {stats.contracts_reused} | ignorados: {stats.contracts_skipped}')
    if args.boletos:
        print(f'COBRANÇAS    {rotulo}: {stats.billings_created} | já existiam: {stats.billings_reused} | ignorados: {stats.billings_skipped}')

    if stats.skips:
        print(f'\nIGNORADOS ({len(stats.skips)}):')
        for motivo in stats.skips[:30]:
            print(f'  - {motivo}')
        if len(stats.skips) > 30:
            print(f'  ... e mais {len(stats.skips) - 30}')

    if not args.apply:
        print('\nNada foi gravado (simulação). Repita com --apply para gravar.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
