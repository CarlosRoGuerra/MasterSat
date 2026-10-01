"""
Importa do SGR para o banco do MasterSat os N primeiros clientes com seus
veículos, rastreadores, contratos, histórico financeiro e arquivos.

Leitura no SGR (só GET, mesmo cliente da POC) + ESCRITA no MasterSat.

Segurança:
  - dry-run é o PADRÃO: sem --apply nada é gravado (roda tudo e dá rollback,
    então já mostra erro de constraint, documento que não fecha e conflito
    que aconteceriam de verdade) e nenhum arquivo é baixado;
  - --apply só é aceito se o banco for local; apontar para outro host exige
    --permitir-banco-remoto, para nunca escrever na produção por acidente;
  - cada cliente é confirmado numa transação curta (checkpoint); uma falha
    não desfaz os outros e --retomar pula o que já foi aplicado;
  - reexecutar SINCRONIZA pela identidade de origem (ver
    docs/migracao-sgr/politica-conflitos.md): o que mudou só na origem é
    aplicado se a política permitir; o que mudou também aqui vira conflito
    (--conflitos / --resolver), nunca sobrescrita.

Uso (a partir de backend/):
  python scripts/sgr_import.py                         # simulação
  python scripts/sgr_import.py --limit 10 --apply      # grava de verdade
  python scripts/sgr_import.py --apply --retomar 12    # retoma a execução 12
  python scripts/sgr_import.py --arquivos --apply      # só baixa PDFs/XMLs pendentes
  python scripts/sgr_import.py --conflitos             # lista a fila de decisões
  python scripts/sgr_import.py --resolver 5 --decisao manter_local \\
      --operador "Fulano" --justificativa "pago no caixa" --apply

Código de saída: 0 concluída (com ou sem pendências listadas), 1 recusa/falha,
2 execução incompleta (coleta ou unidade falhou — rode de novo).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
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
# Mesma história para o MinIO: dentro do compose o host é 'minio', que não
# resolve aqui fora e faz o upload falhar depois de já ter baixado o arquivo.
os.environ.setdefault('MINIO_ENDPOINT', 'localhost:9000')

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import registry_all  # noqa: E402,F401
from app.services.sgr_migration import conflitos  # noqa: E402
from app.services.sgr_migration.client import SGRApiError, SGRClient, SGRError  # noqa: E402
from app.services.sgr_migration.importer import ImportStats, import_poc_result, processar_arquivos  # noqa: E402
from app.services.sgr_migration.poc import SGRIncompleteScan, run_poc  # noqa: E402

_HOSTS_LOCAIS = {'localhost', '127.0.0.1', '::1', 'db'}
_SAIDA = Path(_BACKEND_DIR) / 'scripts' / 'sgr_saida'  # fora do git (.gitignore)


def _banco_descricao() -> tuple[str, str]:
    """(host, descrição sem credencial) do banco alvo."""
    partes = urlsplit(settings.database_url)
    host = (partes.hostname or '').lower()
    return host, f'{host}:{partes.port or 5432}{partes.path}'


def _imprimir_stats(stats: ImportStats, apply: bool, com_boletos: bool, notas: bool) -> None:
    rotulo = 'criados' if apply else 'seriam criados'
    print(f'CLIENTES     {rotulo}: {stats.clients_created} | já existiam: {stats.clients_reused} | ignorados: {stats.clients_skipped} | bloqueados: {stats.clientes_bloqueados}')
    print(f'VEÍCULOS     {rotulo}: {stats.vehicles_created} | já existiam: {stats.vehicles_reused} | ignorados: {stats.vehicles_skipped}')
    print(f'RASTREADORES {rotulo}: {stats.trackers_created} | já existiam: {stats.trackers_reused} | ignorados: {stats.trackers_skipped}')
    print(f'PLANOS       {rotulo}: {stats.plans_created} | já existiam: {stats.plans_reused} | ignorados: {stats.plans_skipped}')
    print(f'CONTRATOS    {rotulo}: {stats.contracts_created} | já existiam: {stats.contracts_reused} | ignorados: {stats.contracts_skipped}')
    if com_boletos:
        print(f'COBRANÇAS    {rotulo}: {stats.billings_created} | atualizadas pela origem: {stats.billings_updated} | '
              f'já existiam: {stats.billings_reused} | não criadas: {stats.billings_skipped}')
        print(f'DOCUMENTOS   conciliados: {stats.documentos_conciliados} | inalterados: {stats.documentos_inalterados} | '
              f'bloqueados: {stats.documentos_bloqueados}'
              + (f"  ({', '.join(f'{m}: {n}' for m, n in sorted(stats.bloqueios.items()))})" if stats.bloqueios else ''))
        if stats.descontos_centavos or stats.linhas_bonificadas:
            print(f'             descontos alocados: R$ {stats.descontos_centavos / 100:,.2f} | '
                  f'linhas bonificadas: {stats.linhas_bonificadas}')
        if stats.billings_replaced:
            print(f'             {stats.billings_replaced} reemissão(ões) cancelada(s) de mensalidade puladas '
                  '(o mês já tem o boleto que valeu)')
    if notas:
        print(f'NOTAS FISCAIS baixadas: {stats.invoices_created} | já existiam: {stats.invoices_reused} | '
              f'não baixadas: {stats.invoices_skipped}')
    if stats.boletos_created or stats.boletos_reused or stats.boletos_skipped:
        print(f'PDF DE BOLETO baixados: {stats.boletos_created} | já existiam: {stats.boletos_reused} | '
              f'não baixados: {stats.boletos_skipped}   (só dos boletos em aberto)')
    if stats.hosts_arquivos:
        print('HOSTS DOS ARQUIVOS (aprovar em SGR_DOWNLOAD_HOSTS antes de baixar):')
        for host, n in sorted(stats.hosts_arquivos.items()):
            print(f'  {host}: {n}')
    if stats.conflitos:
        print(f'CONFLITOS    {stats.conflitos} nesta rodada: '
              + ', '.join(f'{t}: {n}' for t, n in sorted(stats.conflitos_por_tipo.items()))
              + '   → python scripts/sgr_import.py --conflitos')
    if stats.skips:
        print(f'\nOBSERVAÇÕES ({len(stats.skips)}):')
        for motivo in stats.skips[:30]:
            print(f'  - {motivo}')
        if len(stats.skips) > 30:
            print(f'  ... e mais {len(stats.skips) - 30}')


def _gravar_manifesto(stats: ImportStats, destino: str | None) -> Path:
    if destino:
        caminho = Path(destino)
    else:
        _SAIDA.mkdir(parents=True, exist_ok=True)
        sufixo = stats.execucao_id or 'simulacao'
        caminho = _SAIDA / f'manifesto-{sufixo}-{datetime.now():%Y%m%d-%H%M%S}.json'
    caminho.write_text(json.dumps(stats.manifesto, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    return caminho


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--limit', type=int, default=None, help='Nº de clientes (padrão: SGR_MIGRATION_LIMIT)')
    parser.add_argument('--apply', action='store_true', help='Grava de verdade (sem isto é só simulação)')
    parser.add_argument('--permitir-banco-remoto', action='store_true',
                        help='Libera --apply em banco não-local. Use com MUITA atenção.')
    parser.add_argument('--sem-boletos', action='store_true',
                        help='NÃO traz o histórico financeiro (por padrão ele vem junto)')
    parser.add_argument('--notas', action='store_true',
                        help='Pede o XML das notas fiscais como documento do cliente')
    parser.add_argument('--de', type=str, default=None, metavar='AAAA-MM',
                        help='Início do período de cobranças (usa a varredura por período, mais eficiente)')
    parser.add_argument('--ate', type=str, default=None, metavar='AAAA-MM',
                        help='Fim do período de cobranças')
    parser.add_argument('--retomar', type=int, default=None, metavar='EXECUCAO',
                        help='Pula os clientes já aplicados nesta execução com o mesmo dado de origem')
    parser.add_argument('--manifesto', type=str, default=None, metavar='ARQUIVO',
                        help='Onde gravar o manifesto de reconciliação (padrão: scripts/sgr_saida/)')
    parser.add_argument('--arquivos', action='store_true',
                        help='Só processa o outbox de PDFs/XMLs pendentes (não lê o SGR)')
    parser.add_argument('--conflitos', action='store_true', help='Lista os conflitos abertos e sai')
    parser.add_argument('--resolver', type=int, default=None, metavar='CONFLITO')
    parser.add_argument('--decisao', choices=conflitos.DECISOES)
    parser.add_argument('--operador', type=str, default=None)
    parser.add_argument('--justificativa', type=str, default=None)
    args = parser.parse_args()

    host, banco = _banco_descricao()
    local = host in _HOSTS_LOCAIS
    print(f'Banco de destino: {banco}  ({"LOCAL" if local else "REMOTO"})')
    escreve = args.apply or args.resolver is not None
    if escreve and not local and not args.permitir_banco_remoto:
        print('RECUSADO: gravação em banco NÃO-LOCAL sem --permitir-banco-remoto.')
        print('Se a intenção é mesmo escrever nesse banco, repita com a flag explícita.')
        return 1

    if args.conflitos:
        with SessionLocal() as db:
            abertos = conflitos.listar(db)
        print(f'{len(abertos)} conflito(s) aberto(s)')
        for c in abertos:
            print(json.dumps(c, ensure_ascii=False, default=str))
        return 0

    if args.resolver is not None:
        if not args.apply:
            print('RECUSADO: --resolver grava a decisão; repita com --apply.')
            return 1
        with SessionLocal() as db:
            try:
                conflito = conflitos.resolver(db, args.resolver, args.decisao or '',
                                              operador=args.operador or '', justificativa=args.justificativa or '')
                db.commit()
            except conflitos.ResolucaoRecusada as exc:
                db.rollback()
                print(f'RECUSADO: {exc}')
                return 1
        print(f'Conflito #{conflito.id} resolvido: {conflito.resolucao} ({conflito.resolvido_por}).')
        print('Rode a importação de novo para aplicar o que dependia desta decisão.')
        return 0

    if args.arquivos:
        if not args.apply:
            print('RECUSADO: --arquivos baixa e grava; repita com --apply.')
            return 1
        with SessionLocal() as db:
            stats = processar_arquivos(db)
        _imprimir_stats(stats, True, True, True)
        return 0

    limit = args.limit or settings.sgr_migration_limit
    periodo = None
    if args.de and args.ate:
        ano_i, mes_i = (int(x) for x in args.de.split('-'))
        ano_f, mes_f = (int(x) for x in args.ate.split('-'))
        periodo = (date(ano_i, mes_i, 1), date(ano_f, mes_f, 1))
    elif args.de or args.ate:
        print('RECUSADO: --de e --ate andam juntos.')
        return 1
    if args.sem_boletos and periodo:
        print('RECUSADO: --de/--ate definem o período do histórico financeiro — não combinam com --sem-boletos.')
        return 1
    if args.retomar and not args.apply:
        print('RECUSADO: --retomar só faz sentido com --apply.')
        return 1
    com_boletos = not args.sem_boletos

    print(f'Origem: SGR Hinova · {limit} cliente(s) · só clientes e veículos ativos/inadimplentes')
    if not com_boletos:
        print('Histórico financeiro: NÃO (--sem-boletos)')
    elif periodo:
        print(f'Histórico financeiro: {args.de} até {args.ate}')
    else:
        print('Histórico financeiro: completo (paginado por cliente)')
    print(f'Modo: {"GRAVANDO (--apply)" if args.apply else "SIMULAÇÃO (dry-run) — nada será gravado nem baixado"}')
    print()

    print('Lendo do SGR (somente leitura)...')
    sgr = SGRClient()
    try:
        resultado = run_poc(sgr, limit, com_boletos=com_boletos, com_notas=args.notas, periodo=periodo)
    except SGRIncompleteScan as exc:
        print(f'ABORTADO — varredura incompleta: {exc}')
        print('Nada foi gravado. Um mês/página faltando deixaria o histórico incompleto')
        print('sem aviso na tela — rode de novo quando o SGR estabilizar.')
        return 2
    except (SGRError, SGRApiError) as exc:
        print(f'FALHA ao ler o SGR: {exc}')
        return 1
    print(f'  {len(resultado.clients)} cliente(s) elegível(is) (ativo/inadimplente) '
          f'em {resultado.request_count} requisição(ões), {len(resultado.coletas)} coleção(ões) paginada(s).')
    if resultado.clients_out_of_scope:
        total_fora = sum(resultado.clients_out_of_scope.values())
        detalhe = ', '.join(f'{sit}: {n}' for sit, n in sorted(resultado.clients_out_of_scope.items()))
        print(f'  {total_fora} cliente(s) fora do escopo, não migrado(s): {detalhe}')
    if resultado.vehicles_out_of_scope:
        total_fora = sum(resultado.vehicles_out_of_scope.values())
        detalhe = ', '.join(f'{sit}: {n}' for sit, n in sorted(resultado.vehicles_out_of_scope.items()))
        print(f'  {total_fora} veículo(s) não ativo(s), não migrado(s): {detalhe}')
        print('  (as cobranças antigas dessas placas continuam no histórico do cliente)')
    print()

    parametros = {'limit': limit, 'periodo': [args.de, args.ate] if periodo else None,
                  'com_boletos': com_boletos, 'notas': args.notas}
    db = SessionLocal()
    try:
        stats = import_poc_result(db, resultado, dry_run=not args.apply, retomar_de=args.retomar,
                                  parametros=parametros)
    except Exception as exc:  # noqa: BLE001 — qualquer erro precisa desfazer a transação
        db.rollback()
        print(f'FALHA ao importar: {type(exc).__name__}: {exc}')
        return 1
    finally:
        db.close()

    _imprimir_stats(stats, args.apply, com_boletos, args.notas)
    caminho = _gravar_manifesto(stats, args.manifesto)
    manifesto = stats.manifesto
    print()
    print(f'EXECUÇÃO {"#" + str(stats.execucao_id) if stats.execucao_id else "(simulação)"}: {stats.status_execucao}')
    print(f'  diferenças origem × MasterSat: {len(manifesto["diferencas"])} | documentos bloqueados: '
          f'{len(manifesto["documentos_bloqueados"])} | coleções incompletas: {len(manifesto["coletas"]["incompletas"])}')
    print(f'  manifesto (sem dado pessoal): {caminho}')
    if not args.apply:
        print('\nNada foi gravado (simulação). Repita com --apply para gravar.')
    return 2 if stats.status_execucao in ('incompleta', 'falhou') else 0


if __name__ == '__main__':
    raise SystemExit(main())
