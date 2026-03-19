from datetime import date

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
    return {
        'clients': {
            'active': db.scalar(select(func.count()).select_from(Client).where(Client.status == ClientStatus.ACTIVE, Client.is_deleted.is_(False))) or 0,
            'inactive': db.scalar(select(func.count()).select_from(Client).where(Client.status == ClientStatus.INACTIVE, Client.is_deleted.is_(False))) or 0,
            'delinquent': db.scalar(select(func.count()).select_from(Client).where(Client.status == ClientStatus.DELINQUENT, Client.is_deleted.is_(False))) or 0,
        },
        'vehicles': {
            'total': db.scalar(select(func.count()).select_from(Vehicle).where(Vehicle.is_deleted.is_(False))) or 0,
        },
        'trackers': {
            'installed': db.scalar(select(func.count()).select_from(Tracker).where(Tracker.status == TrackerStatus.INSTALLED, Tracker.is_deleted.is_(False))) or 0,
            'stock': db.scalar(select(func.count()).select_from(Tracker).where(Tracker.status == TrackerStatus.STOCK, Tracker.is_deleted.is_(False))) or 0,
            'maintenance': db.scalar(select(func.count()).select_from(Tracker).where(Tracker.status == TrackerStatus.MAINTENANCE, Tracker.is_deleted.is_(False))) or 0,
        },
        'service_orders': {
            'open': db.scalar(select(func.count()).select_from(ServiceOrder).where(ServiceOrder.status == OrderStatus.OPEN, ServiceOrder.is_deleted.is_(False))) or 0,
            'in_progress': db.scalar(select(func.count()).select_from(ServiceOrder).where(ServiceOrder.status == OrderStatus.IN_PROGRESS, ServiceOrder.is_deleted.is_(False))) or 0,
            'completed': db.scalar(select(func.count()).select_from(ServiceOrder).where(ServiceOrder.status == OrderStatus.COMPLETED, ServiceOrder.is_deleted.is_(False))) or 0,
        },
        'finance': {
            'pending_count': db.scalar(select(func.count()).select_from(Billing).where(Billing.status == BillingStatus.PENDING, Billing.is_deleted.is_(False))) or 0,
            'overdue_count': db.scalar(select(func.count()).select_from(Billing).where(Billing.status == BillingStatus.OVERDUE, Billing.is_deleted.is_(False))) or 0,
            'received_month': float(
                db.scalar(select(func.coalesce(func.sum(Billing.amount), 0)).where(Billing.status == BillingStatus.PAID, Billing.is_deleted.is_(False))) or 0
            ),
        },
        'reference_date': today.isoformat(),
    }
