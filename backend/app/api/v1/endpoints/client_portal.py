from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import asc, func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus, UserRole, VehicleStatus
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.client_portal import (
    ClientBillingOut,
    ClientDashboardOut,
    ClientDashboardSummaryOut,
    ClientProfileOut,
    ClientVehicleOut,
)

router = APIRouter()


@router.get('/dashboard', response_model=ClientDashboardOut)
def client_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.CLIENT)),
):
    if not current_user.client_id:
        raise HTTPException(status_code=404, detail='Cliente vinculado não encontrado')

    client = db.get(Client, current_user.client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail='Cliente vinculado não encontrado')

    vehicles = db.scalars(
        select(Vehicle)
        .where(Vehicle.client_id == client.id, Vehicle.is_deleted.is_(False))
        .order_by(asc(Vehicle.plate))
    ).all()

    recent_billings = db.scalars(
        select(Billing)
        .where(Billing.client_id == client.id, Billing.is_deleted.is_(False))
        .order_by(Billing.due_date.desc())
        .limit(6)
    ).all()

    today = date.today()
    pending_billings = db.scalar(
        select(func.count(Billing.id)).where(
            Billing.client_id == client.id,
            Billing.status == BillingStatus.PENDING,
            Billing.is_deleted.is_(False),
        )
    ) or 0
    overdue_billings = db.scalar(
        select(func.count(Billing.id)).where(
            Billing.client_id == client.id,
            Billing.due_date < today,
            Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
            Billing.is_deleted.is_(False),
        )
    ) or 0
    total_open_amount = db.scalar(
        select(func.coalesce(func.sum(Billing.amount), 0)).where(
            Billing.client_id == client.id,
            Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
            Billing.is_deleted.is_(False),
        )
    ) or 0

    return ClientDashboardOut(
        profile=ClientProfileOut(
            id=client.id,
            name=client.name,
            cpf_cnpj=client.cpf_cnpj,
            email=client.email,
            phone=client.phone,
            city=client.city,
            state=client.state,
            status=client.status,
        ),
        summary=ClientDashboardSummaryOut(
            total_vehicles=len(vehicles),
            active_vehicles=sum(1 for item in vehicles if item.status == VehicleStatus.ACTIVE),
            pending_billings=int(pending_billings),
            overdue_billings=int(overdue_billings),
            total_open_amount=float(total_open_amount),
        ),
        vehicles=[
            ClientVehicleOut(
                id=item.id,
                plate=item.plate,
                model=item.model,
                brand=item.brand,
                year=item.year,
                status=item.status,
            )
            for item in vehicles
        ],
        recent_billings=[
            ClientBillingOut(
                id=item.id,
                amount=float(item.amount),
                due_date=item.due_date,
                status=item.status,
                payment_date=item.payment_date,
                payment_method=item.payment_method,
            )
            for item in recent_billings
        ],
    )
