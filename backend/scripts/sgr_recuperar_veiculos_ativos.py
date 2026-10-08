#!/usr/bin/env python3
"""Simula ou recupera exclusivamente os cadastros ATIVO explicitamente selecionados."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import registry_all  # noqa: F401: registra todas as tabelas
from app.services.sgr_migration.active_vehicle_recovery import (
    RecoveryError, collect_source, normalize_identifiers, recover_active_vehicles,
)
from app.services.sgr_migration.client import SGRApiError, SGRClient, SGRError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--identificadores', nargs='+', required=True)
    parser.add_argument('--operator', required=True, help='Nome do operador para auditoria.')
    parser.add_argument('--apply', action='store_true', help='Confirma o lote. Sem esta opção, tudo é desfeito.')
    args = parser.parse_args()
    sgr = None
    try:
        identifiers = normalize_identifiers(args.identificadores)
        print('Consultando situação atual e responsáveis no SGR...', flush=True)
        sgr = SGRClient()
        source = collect_source(sgr, identifiers)
        with SessionLocal() as db:
            result = recover_active_vehicles(db, source, identifiers, operator=args.operator)
            if args.apply:
                db.commit()
            else:
                db.rollback()
        print(json.dumps({'modo': 'APLICADO' if args.apply else 'SIMULACAO_SEM_GRAVACAO', 'veiculos': result},
                         ensure_ascii=False, indent=2))
        return 0
    except (RecoveryError, SGRError, SGRApiError) as exc:
        message = str(exc)
        for secret in (settings.sgr_password, settings.sgr_username, settings.sgr_api_key, settings.sgr_cod_mobile):
            if secret:
                message = message.replace(secret, '[omitido]')
        print(f'Lote não aplicado: {message[:600]}', file=sys.stderr)
        return 1
    except SQLAlchemyError:
        # Exceções SQL incluem parâmetros pessoais; não imprimir a consulta.
        print('Lote não aplicado: falha no banco. Confira a migração e os vínculos existentes.', file=sys.stderr)
        return 1
    finally:
        if sgr:
            sgr.session.close()


if __name__ == '__main__':
    raise SystemExit(main())
