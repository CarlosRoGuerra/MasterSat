#!/usr/bin/env python3
"""Inclui no fechamento as avulsas do antigo cadastro manual do rastreador.

Sem --aplicar, apenas confere. A conversão não emite boletos nem gera o fechamento.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import SessionLocal
from app.models import registry_all  # noqa: F401
from app.models.client import Client
from app.services.titulo_bancario import PoliticaBancariaError
from app.services.tracker_charge_recovery import (
    correct_tracker_charge_due_date, find_tracker_charge_candidates, recover_tracker_charges,
)


def parse_month(value: str) -> date:
    try:
        parsed = date.fromisoformat(value + '-01')
        if parsed.strftime('%Y-%m') != value:
            raise ValueError
        return parsed
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Use YYYY-MM para o mês do serviço.') from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument('--cliente', help='Nome completo e exato do cliente atendido.')
    scope.add_argument('--cliente-id', type=int)
    parser.add_argument('--mes-servico', required=True, type=parse_month)
    parser.add_argument('--cobranca-id', type=int, action='append', help='Opcional; pode ser repetido.')
    parser.add_argument('--adicional-vencimento', type=date.fromisoformat,
        help='Corrige a interpretação da data de um adicional já cadastrado, sem convertê-lo de novo.')
    parser.add_argument('--valor', type=Decimal, help='Valor exato do adicional a corrigir.')
    parser.add_argument('--aplicar', action='store_true')
    args = parser.parse_args(argv)
    if bool(args.adicional_vencimento) != (args.valor is not None) or (args.adicional_vencimento and args.cobranca_id):
        parser.error('Informe --adicional-vencimento e --valor juntos, sem --cobranca-id.')
    try:
        with SessionLocal() as db:
            query = select(Client).where(Client.is_deleted.is_(False))
            if args.cliente_id is not None:
                query = query.where(Client.id == args.cliente_id)
            else:
                query = query.where(func.lower(func.trim(Client.name)) == args.cliente.strip().lower())
            clients = db.scalars(query).all()
            if len(clients) != 1:
                raise ValueError('Cliente não encontrado ou nome ambíguo. Confira o nome ou use --cliente-id.')
            client = clients[0]
            if args.adicional_vencimento:
                result = [correct_tracker_charge_due_date(db, client_id=client.id,
                    service_month=args.mes_servico, due_date=args.adicional_vencimento,
                    amount=args.valor, apply=args.aplicar)]
            else:
                ids = args.cobranca_id or find_tracker_charge_candidates(db, client.id, args.mes_servico)
                if not ids:
                    raise ValueError('Nenhuma avulsa com rastreador e veículo encontrada para este cliente e mês. '
                                     'Para uma cobrança cadastrada em outro mês, informe --cobranca-id.')
                result = recover_tracker_charges(
                    db, ids, args.mes_servico, apply=args.aplicar, expected_client_id=client.id,
                )
            client_info = {'id': client.id, 'nome': client.name}
            if args.aplicar:
                db.commit()
            else:
                db.rollback()
        print(json.dumps({
            'modo': 'APLICADO' if args.aplicar else 'SIMULACAO_SEM_GRAVACAO',
            'cliente': client_info, 'cobrancas': result,
            'proximo_passo': 'Simule o fechamento do mês do serviço informado.',
        }, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, InvalidOperation, PoliticaBancariaError) as exc:
        print(f'Não aplicado: {exc}', file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print('Não aplicado: falha no banco; a transação foi desfeita.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
