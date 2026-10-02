from datetime import date, timedelta

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.dashboard_cache import get_dashboard, put_dashboard
from app.core.permissions import Capability, has_capability, roles_with
from app.db.session import get_db
from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus, ClientStatus, OrderStatus, TrackerStatus
from app.models.service_order import ServiceOrder
from app.models.tracker import Tracker
from app.models.user import User
from app.models.vehicle import Vehicle

router = APIRouter()


@router.get('/')
def dashboard(
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*roles_with(Capability.REGISTRY_READ))),
):
    role = current_user.role.value if hasattr(current_user.role, 'value') else str(current_user.role)
    cached = get_dashboard(role)
    if cached is not None:
        response.headers['X-Dashboard-Cache'] = 'HIT'
        return cached
    response.headers['X-Dashboard-Cache'] = 'MISS'
    today = date.today()
    first_of_month = today.replace(day=1)
    first_of_prev = date(
        today.year if today.month > 1 else today.year - 1,
        today.month - 1 if today.month > 1 else 12,
        1,
    )
    ver_financeiro = has_capability(current_user.role, Capability.FINANCIAL_READ)

    # Six client metrics in one table scan; retain the original status-only
    # denominator, which excludes any future/unknown enum value.
    active, inactive, delinquent, suspended, new_this, new_prev = db.execute(
        select(
            func.count().filter(Client.status == ClientStatus.ACTIVE),
            func.count().filter(Client.status == ClientStatus.INACTIVE),
            func.count().filter(Client.status == ClientStatus.DELINQUENT),
            func.count().filter(Client.status == ClientStatus.SUSPENDED),
            func.count().filter(
                Client.created_at >= first_of_month,
                Client.created_at < today + timedelta(days=1),
            ),
            func.count().filter(
                Client.created_at >= first_of_prev,
                Client.created_at < first_of_month,
            ),
        ).where(Client.is_deleted.is_(False))
    ).one()
    clients = {
        'active': active,
        'inactive': inactive,
        'delinquent': delinquent,
        'suspended': suspended,
        'total': active + inactive + delinquent + suspended,
        'new_this_month': new_this,
        'new_prev_month': new_prev,
    }

    vehicles_total = db.scalar(
        select(func.count()).select_from(Vehicle).where(Vehicle.is_deleted.is_(False))
    ) or 0
    # Count distinct linked vehicles, not trackers: one vehicle may have two.
    vehicles_with_tracker = db.scalar(
        select(func.count(func.distinct(Tracker.vehicle_id)))
        .join(Vehicle, Vehicle.id == Tracker.vehicle_id)
        .where(
            Tracker.status == TrackerStatus.INSTALLED,
            Tracker.is_deleted.is_(False),
            Vehicle.is_deleted.is_(False),
        )
    ) or 0

    installed, stock, maintenance = db.execute(
        select(
            func.count().filter(Tracker.status == TrackerStatus.INSTALLED),
            func.count().filter(Tracker.status == TrackerStatus.STOCK),
            func.count().filter(Tracker.status == TrackerStatus.MAINTENANCE),
        ).where(Tracker.is_deleted.is_(False))
    ).one()
    open_orders, in_progress, completed = db.execute(
        select(
            func.count().filter(ServiceOrder.status == OrderStatus.OPEN),
            func.count().filter(ServiceOrder.status == OrderStatus.IN_PROGRESS),
            func.count().filter(ServiceOrder.status == OrderStatus.COMPLETED),
        ).where(ServiceOrder.is_deleted.is_(False))
    ).one()

    finance = None
    upcoming_rows = []
    overdue_rows = []
    if ver_financeiro:
        # Preserve cash basis and the legacy paid_amount fallback.
        received_amount = func.coalesce(Billing.paid_amount, Billing.amount)
        pending, overdue, current, previous = db.execute(
            select(
                func.count().filter(Billing.status == BillingStatus.PENDING),
                func.count().filter(Billing.status == BillingStatus.OVERDUE),
                func.coalesce(func.sum(received_amount).filter(
                    Billing.status == BillingStatus.PAID,
                    Billing.payment_date >= first_of_month,
                    Billing.payment_date < today + timedelta(days=1),
                ), 0),
                func.coalesce(func.sum(received_amount).filter(
                    Billing.status == BillingStatus.PAID,
                    Billing.payment_date >= first_of_prev,
                    Billing.payment_date < first_of_month,
                ), 0),
            ).where(Billing.is_deleted.is_(False))
        ).one()
        received_this = float(current)
        received_prev = float(previous)
        delta_received = round(received_this - received_prev, 2)
        finance = {
            'pending_count': pending,
            'overdue_count': overdue,
            'received_month': received_this,
            'received_prev_month': received_prev,
            'delta_received': delta_received,
            'delta_pct': round((delta_received / received_prev * 100) if received_prev else 0, 1),
        }

        def billings(*conditions, order):
            return db.execute(
                select(Billing.id, Billing.due_date, Billing.amount, Client.name.label('client_name'))
                .join(Client, Client.id == func.coalesce(Billing.payer_client_id, Billing.client_id))
                .where(Billing.is_deleted.is_(False), Client.is_deleted.is_(False), *conditions)
                .order_by(order, Billing.id.asc())
                .limit(5)
            ).all()

        upcoming_rows = billings(
            Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
            Billing.due_date >= today,
            Billing.due_date <= today + timedelta(days=7),
            order=Billing.due_date.asc(),
        )
        overdue_rows = billings(
            Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
            Billing.due_date < today,
            order=Billing.due_date.asc(),
        )

    def serialize_billing_rows(rows):
        return [
            {
                'id': row.id,
                'client_name': row.client_name,
                'amount': float(row.amount),
                'due_date': row.due_date.isoformat(),
                'days_until': (row.due_date - today).days,
            }
            for row in rows
        ]

    payload = {
        'clients': clients,
        'vehicles': {
            'total': vehicles_total,
            'with_tracker': vehicles_with_tracker,
            'without_tracker': max(vehicles_total - vehicles_with_tracker, 0),
        },
        'trackers': {'installed': installed, 'stock': stock, 'maintenance': maintenance},
        'service_orders': {
            'open': open_orders,
            'in_progress': in_progress,
            'completed': completed,
        },
        'finance': finance,
        'upcoming_billings': serialize_billing_rows(upcoming_rows),
        'overdue_billings': serialize_billing_rows(overdue_rows),
        'reference_date': today.isoformat(),
    }
    put_dashboard(role, payload)
    return payload
