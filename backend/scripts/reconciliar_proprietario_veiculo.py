#!/usr/bin/env python3
"""Reconcilia cliente do rastreador/contrato com o proprietário atual da placa."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import registry_all  # noqa: F401
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import UserRole
from app.models.tracker import Tracker
from app.models.user import User
from app.models.vehicle import Vehicle
from app.services.vehicle_client_change import change_vehicle_client


def reconcile(db: Session, *, plate: str, imei: str, expected_client_name: str, operator: str) -> dict:
    plate = plate.strip().upper().replace('-', '').replace(' ', '')
    if not plate or not imei.strip() or not expected_client_name.strip() or not 1 <= len(operator.strip()) <= 120:
        raise ValueError('Informe placa, IMEI, cliente esperado e operador (até 120 caracteres).')
    vehicle = db.scalar(select(Vehicle).where(Vehicle.plate == plate, Vehicle.is_deleted.is_(False))
                        .with_for_update().execution_options(populate_existing=True))
    if not vehicle:
        raise HTTPException(404, 'Veículo não encontrado.')
    client = db.get(Client, vehicle.client_id)
    normalize_name = lambda value: ' '.join(value.split()).casefold()
    if not client or client.is_deleted or normalize_name(client.name) != normalize_name(expected_client_name):
        raise HTTPException(409, 'O proprietário atual da placa não é o cliente esperado. Nenhuma alteração aplicada.')
    tracker = db.scalar(select(Tracker).where(Tracker.imei == imei.strip(), Tracker.is_deleted.is_(False)))
    if not tracker or tracker.vehicle_id != vehicle.id:
        raise HTTPException(409, 'O IMEI não está vinculado a esta placa. Confira o vínculo antes de reconciliar.')
    previous_tracker_client = tracker.client_id
    # Operação local pela credencial do banco; não inventa um ID de usuário web.
    actor = User(name=operator.strip(), role=UserRole.ADMIN)
    contracts, trackers = change_vehicle_client(
        db, vehicle, client.id, actor, expected_tracker_id=tracker.id,
        method='SCRIPT', path='/scripts/reconciliar_proprietario_veiculo',
    )
    db.flush()
    active_contracts = db.scalars(select(Contract).where(
        Contract.vehicle_id == vehicle.id, Contract.status == 'ativo', Contract.is_deleted.is_(False),
    ).order_by(Contract.id)).all()
    return {
        'placa': vehicle.plate, 'vehicle_id': vehicle.id,
        'cliente': {'id': client.id, 'nome': client.name},
        'rastreador': {'id': tracker.id, 'imei': tracker.imei, 'cliente_anterior_id': previous_tracker_client,
                       'cliente_id': tracker.client_id, 'status': tracker.status.value,
                       'data_instalacao': tracker.install_date.isoformat() if tracker.install_date else None},
        'contratos_atualizados': contracts, 'rastreadores_atualizados': trackers,
        'contratos_ativos': [{'id': c.id, 'client_id': c.client_id, 'plan_id': c.plan_id,
                              'inicio': c.start_date.isoformat(), 'dia_vencimento': c.billing_day,
                              'assinado': c.signed} for c in active_contracts],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--placa', required=True)
    parser.add_argument('--imei', required=True)
    parser.add_argument('--cliente-esperado', required=True)
    parser.add_argument('--operator', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        with SessionLocal() as db:
            result = reconcile(db, plate=args.placa, imei=args.imei,
                               expected_client_name=args.cliente_esperado, operator=args.operator)
            if args.apply:
                db.commit()
            else:
                db.rollback()
        print(json.dumps({'modo': 'APLICADO' if args.apply else 'SIMULACAO_SEM_GRAVACAO', **result},
                         ensure_ascii=False, indent=2))
        return 0
    except (HTTPException, ValueError) as exc:
        print(f'Não aplicado: {exc.detail if isinstance(exc, HTTPException) else exc}', file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print('Não aplicado: falha no banco; a transação foi desfeita.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
