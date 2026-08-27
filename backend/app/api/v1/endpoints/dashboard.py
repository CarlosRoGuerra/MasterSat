from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus, ClientStatus, OrderStatus, TrackerStatus, UserRole
from app.models.service_order import ServiceOrder
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle

router = APIRouter()


@router.get('/')
def dashboard(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL)),
):
    today = date.today()
    first_of_month = today.replace(day=1)
    first_of_prev = date(today.year if today.month > 1 else today.year - 1,
                         today.month - 1 if today.month > 1 else 12, 1)

    # ── Finance deltas (current vs previous calendar month) ──────────────
    def _received(date_from: date, date_to: date) -> float:
        return float(db.scalar(
            select(func.coalesce(func.sum(Billing.paid_amount), 0))
            .where(
                Billing.status == BillingStatus.PAID,
                Billing.is_deleted.is_(False),
                Billing.payment_date >= date_from,
                Billing.payment_date < date_to,
            )
        ) or 0)

    received_this = _received(first_of_month, today + timedelta(days=1))
    received_prev = _received(first_of_prev, first_of_month)
    delta_received = round(received_this - received_prev, 2)
    delta_pct = round((delta_received / received_prev * 100) if received_prev else 0, 1)

    # ── New clients this month vs previous ───────────────────────────────
    def _new_clients(date_from: date, date_to: date) -> int:
        return db.scalar(
            select(func.count()).select_from(Client)
            .where(Client.is_deleted.is_(False),
                   Client.created_at >= date_from,
                   Client.created_at < date_to)
        ) or 0

    new_this = _new_clients(first_of_month, today + timedelta(days=1))
    new_prev = _new_clients(first_of_prev, first_of_month)

    # ── Upcoming billings (next 7 days, max 5) ───────────────────────────
    upcoming_rows = db.execute(
        select(Billing.id, Billing.due_date, Billing.amount, Client.name.label('client_name'))
        .join(
            Client,
            Client.id == func.coalesce(Billing.payer_client_id, Billing.client_id),
        )
        .where(
            Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
            Billing.is_deleted.is_(False),
            Client.is_deleted.is_(False),
            Billing.due_date <= today + timedelta(days=7),
        )
        .order_by(Billing.due_date.asc())
        .limit(5)
    ).all()

    return {
        'clients': {
            'active':    db.scalar(select(func.count()).select_from(Client).where(Client.status == ClientStatus.ACTIVE,    Client.is_deleted.is_(False))) or 0,
            'inactive':  db.scalar(select(func.count()).select_from(Client).where(Client.status == ClientStatus.INACTIVE,  Client.is_deleted.is_(False))) or 0,
            'delinquent':db.scalar(select(func.count()).select_from(Client).where(Client.status == ClientStatus.DELINQUENT,Client.is_deleted.is_(False))) or 0,
            'new_this_month': new_this,
            'new_prev_month': new_prev,
        },
        'vehicles': {
            'total': db.scalar(select(func.count()).select_from(Vehicle).where(Vehicle.is_deleted.is_(False))) or 0,
        },
        'trackers': {
            'installed':   db.scalar(select(func.count()).select_from(Tracker).where(Tracker.status == TrackerStatus.INSTALLED,  Tracker.is_deleted.is_(False))) or 0,
            'stock':       db.scalar(select(func.count()).select_from(Tracker).where(Tracker.status == TrackerStatus.STOCK,       Tracker.is_deleted.is_(False))) or 0,
            'maintenance': db.scalar(select(func.count()).select_from(Tracker).where(Tracker.status == TrackerStatus.MAINTENANCE, Tracker.is_deleted.is_(False))) or 0,
        },
        'service_orders': {
            'open':       db.scalar(select(func.count()).select_from(ServiceOrder).where(ServiceOrder.status == OrderStatus.OPEN,        ServiceOrder.is_deleted.is_(False))) or 0,
            'in_progress':db.scalar(select(func.count()).select_from(ServiceOrder).where(ServiceOrder.status == OrderStatus.IN_PROGRESS, ServiceOrder.is_deleted.is_(False))) or 0,
            'completed':  db.scalar(select(func.count()).select_from(ServiceOrder).where(ServiceOrder.status == OrderStatus.COMPLETED,   ServiceOrder.is_deleted.is_(False))) or 0,
        },
        'finance': {
            'pending_count':    db.scalar(select(func.count()).select_from(Billing).where(Billing.status == BillingStatus.PENDING, Billing.is_deleted.is_(False))) or 0,
            'overdue_count':    db.scalar(select(func.count()).select_from(Billing).where(Billing.status == BillingStatus.OVERDUE, Billing.is_deleted.is_(False))) or 0,
            'received_month':   received_this,
            'received_prev_month': received_prev,
            'delta_received':   delta_received,
            'delta_pct':        delta_pct,
        },
        'upcoming_billings': [
            {
                'id': r.id,
                'client_name': r.client_name,
                'amount': float(r.amount),
                'due_date': r.due_date.isoformat(),
                'days_until': (r.due_date - today).days,
            }
            for r in upcoming_rows
        ],
        'reference_date': today.isoformat(),
    }
