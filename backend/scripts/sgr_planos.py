"""
Mostra os PLANOS e o HISTÓRICO DE COBRANÇA do SGR — SOMENTE LEITURA.

É o retrato do que a API do SGR entrega hoje para montar Plano/Contrato no
MasterSat, sem depender de nenhuma liberação extra da Hinova:

  - planos (grupo de mensalidade) com valor, que é para onde aponta o
    `cod_grupo_vinculo` de cada vínculo;
  - grupos de adesão (taxa de instalação) e condições de pagamento;
  - dias de vencimento;
  - boletos de um cliente, com a discriminação por placa.

Não grava nada, nem no SGR nem no banco do MasterSat.

Uso (a partir de backend/):
  python scripts/sgr_planos.py
  python scripts/sgr_planos.py --boletos 5      # amostra de 5 clientes
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, _BACKEND_DIR)
os.chdir(_BACKEND_DIR)  # pydantic-settings acha o .env a partir do cwd

# O console do Windows abre em cp1252 e estoura UnicodeEncodeError com acento.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass

from app.services.sgr_migration.client import SGRApiError, SGRClient, SGRError  # noqa: E402


def secao(titulo: str) -> None:
    print()
    print('=' * 72)
    print(titulo)
    print('=' * 72)


def tabela(client: SGRClient, titulo: str, metodo) -> None:
    secao(titulo)
    try:
        registros = metodo()
    except (SGRError, SGRApiError) as exc:
        print(f'INDISPONÍVEL: {exc}')
        return
    for item in registros:
        print(f'  {json.dumps(item, ensure_ascii=False)}')
    print(f'  ({len(registros)} registro(s))')


def boletos(client: SGRClient, amostra: int) -> None:
    secao(f'HISTÓRICO DE COBRANÇA — amostra de {amostra} cliente(s)')
    clientes = client.buscar_clientes(total=amostra, indice=0)
    for cliente in clientes:
        cpf = cliente.get('cpf_cliente')
        nome = str(cliente.get('nome_cliente') or '')[:34]
        situacao = (cliente.get('situacao') or {}).get('descricao', '?')
        try:
            todos = client.buscar_boletos_cliente(cpf)
            abertos = client.buscar_boletos_abertos_cliente(cpf)
        except (SGRError, SGRApiError) as exc:
            print(f'  {nome}: FALHA — {exc}')
            continue

        por_situacao: dict[str, int] = {}
        for boleto in todos:
            descricao = (boleto.get('situacao') or {}).get('descricao', '?')
            por_situacao[descricao] = por_situacao.get(descricao, 0) + 1

        print(f'\n  {nome} [{situacao}]')
        print(f'    {len(todos)} boleto(s) | {len(abertos)} em aberto | {por_situacao}')
        if todos:
            exemplo = todos[0]
            print(f"    exemplo: {exemplo.get('mes_referente')} | R$ {exemplo.get('valor')} | "
                  f"venc {exemplo.get('data_vencimento')} | {(exemplo.get('situacao') or {}).get('descricao')}")
            for item in (exemplo.get('discriminacao') or [])[:3]:
                print(f"       placa {item.get('placa')}: R$ {item.get('valor')} ({item.get('mes_referente')})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--boletos', type=int, default=3, help='Nº de clientes na amostra de boletos (0 pula)')
    args = parser.parse_args()

    print('Consultando o SGR (somente leitura)...')
    client = SGRClient()
    try:
        client.authenticate()
    except (SGRError, SGRApiError) as exc:
        print(f'FALHA na autenticação com o SGR: {exc}')
        print('\nDica: a conta de integração recusa acesso fora de dia útil.')
        return 1

    tabela(client, 'PLANOS (grupo de mensalidade) — valor mensal', client.get_grupo_mensalidade)
    tabela(client, 'GRUPOS DE ADESÃO — taxa de instalação', client.get_grupo_adesao)
    tabela(client, 'CONDIÇÕES DE PAGAMENTO', client.get_condicao_pagamento)
    tabela(client, 'DIAS DE VENCIMENTO', client.get_vencimento)

    if args.boletos:
        boletos(client, args.boletos)

    print(f'\nRequisições feitas: {client.request_count}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
