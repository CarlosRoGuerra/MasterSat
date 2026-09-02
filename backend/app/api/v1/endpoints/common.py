"""Helpers de lookup compartilhados entre endpoints (evita duplicar _get_*_or_404)."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.billing import Billing
from app.models.client import Client
from app.models.vehicle import Vehicle


def get_client_or_404(client_id: int, db: Session) -> Client:
    client = db.get(Client, client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail='Cliente não encontrado')
    return client


def get_vehicle_or_404(vehicle_id: int, db: Session) -> Vehicle:
    vehicle = db.get(Vehicle, vehicle_id)
    if not vehicle or vehicle.is_deleted:
        raise HTTPException(status_code=404, detail='Veículo não encontrado')
    return vehicle


def get_billing_or_404(billing_id: int, db: Session) -> Billing:
    billing = db.get(Billing, billing_id)
    if not billing or billing.is_deleted:
        raise HTTPException(status_code=404, detail='Cobrança não encontrada')
    return billing
