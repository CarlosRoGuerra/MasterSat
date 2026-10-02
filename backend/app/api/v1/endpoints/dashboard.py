from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
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
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*roles_with(Capability.REGISTRY_READ))),
):
    today = date.today()
    first_of_month = today.replace(day=1)
    first_of_prev = date(today.year if today.month > 1 else today.year - 1,
                         today.month - 1 if today.month > 1 else 12, 1)

    # Perfil sem FINANCIAL_READ (operacional): 'finance' volta null e
    # 'upcoming_billings' vazio — as chaves continuam no payload, só sem
    # valores/títulos. Mesmo corte de /billings e /reports (SEC-01).
    ver_financeiro = has_capability(current_user.role, Capability.FINANCIAL_READ)

    # ── Finance deltas (current vs previous calendar month) ──────────────
    def _finance() -> dict:
        received = func.coalesce(Billing.paid_amount, Billing.amount)
        pending, overdue, amount_this, amount_prev = db.execute(
            select(
                func.count(case((Billing.status == BillingStatus.PENDING, Billing.id))),
                func.count(case((Billing.status == BillingStatus.OVERDUE, Billing.id))),
                func.coalesce(func.sum(case((
                    (Billing.status == BillingStatus.PAID)
                    & (Billing.payment_date >= first_of_month)
                    & (Billing.payment_date < today + timedelta(days=1)), received,
                ), else_=0)), 0),
                func.coalesce(func.sum(case((
                    (Billing.status == BillingStatus.PAID)
                    & (Billing.payment_date >= first_of_prev)
                    & (Billing.payment_date < first_of_month), received,
                ), else_=0)), 0),
            ).where(Billing.is_deleted.is_(False))
        ).one()
        received_this = float(amount_this)
        received_prev = float(amount_prev)
        delta_received = round(received_this - received_prev, 2)
        delta_pct = round((delta_received / received_prev * 100) if received_prev else 0, 1)
        return {
            'pending_count':    pending,
            'overdue_count':    overdue,
            'received_month':   received_this,
            'received_prev_month': received_prev,
            'delta_received':   delta_received,
            'delta_pct':        delta_pct,
        }

    # ── New clients this month vs previous ───────────────────────────────
    client_counts = db.execute(
        select(
            func.count(case((Client.status == ClientStatus.ACTIVE, Client.id))),
            func.count(case((Client.status == ClientStatus.INACTIVE, Client.id))),
            func.count(case((Client.status == ClientStatus.DELINQUENT, Client.id))),
            func.count(case((Client.status == ClientStatus.SUSPENDED, Client.id))),
            func.count(case(((Client.created_at >= first_of_month) &
                             (Client.created_at < today + timedelta(days=1)), Client.id))),
            func.count(case(((Client.created_at >= first_of_prev) &
                             (Client.created_at < first_of_month), Client.id))),
        ).where(Client.is_deleted.is_(False))
    ).one()
    new_this, new_prev = client_counts[4:]

    # ── Próximos vencimentos × vencidas (PROD-02) ───────────────────────
    # Antes era uma lista só (vencimento <= hoje+7, mais antigo primeiro,
    # 5 itens): cinco atrasos antigos escondiam tudo que vence na semana.
    def _billings(*condicoes, ordem, limite: int = 5):
        if not ver_financeiro:
            return []
        return db.execute(
            select(Billing.id, Billing.due_date, Billing.amount, Client.name.label('client_name'))
            .join(
                Client,
                Client.id == func.coalesce(Billing.payer_client_id, Billing.client_id),
            )
            .where(Billing.is_deleted.is_(False), Client.is_deleted.is_(False), *condicoes)
            .order_by(ordem, Billing.id.asc())
            .limit(limite)
        ).all()

    upcoming_rows = _billings(
        Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
        Billing.due_date >= today,
        Billing.due_date <= today + timedelta(days=7),
        ordem=Billing.due_date.asc(),
    )
    overdue_rows = _billings(
        Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
        Billing.due_date < today,
        ordem=Billing.due_date.asc(),
    )

    clientes = dict(zip(('active', 'inactive', 'delinquent', 'suspended'), client_counts[:4]))
    vehicles_total = db.scalar(select(func.count()).select_from(Vehicle).where(Vehicle.is_deleted.is_(False))) or 0
    # Veículos DISTINTOS com rastreador instalado vinculado — não a contagem
    # de rastreadores (dois rastreadores no mesmo veículo contavam dois).
    vehicles_with_tracker = db.scalar(
        select(func.count(func.distinct(Tracker.vehicle_id)))
        .join(Vehicle, Vehicle.id == Tracker.vehicle_id)
        .where(
            Tracker.status == TrackerStatus.INSTALLED,
            Tracker.is_deleted.is_(False),
            Vehicle.is_deleted.is_(False),
        )
    ) or 0

    tracker_counts = db.execute(
        select(
            func.count(case((Tracker.status == TrackerStatus.INSTALLED, Tracker.id))),
            func.count(case((Tracker.status == TrackerStatus.STOCK, Tracker.id))),
            func.count(case((Tracker.status == TrackerStatus.MAINTENANCE, Tracker.id))),
        ).where(Tracker.is_deleted.is_(False))
    ).one()
    order_counts = db.execute(
        select(
            func.count(case((ServiceOrder.status == OrderStatus.OPEN, ServiceOrder.id))),
            func.count(case((ServiceOrder.status == OrderStatus.IN_PROGRESS, ServiceOrder.id))),
            func.count(case((ServiceOrder.status == OrderStatus.COMPLETED, ServiceOrder.id))),
        ).where(ServiceOrder.is_deleted.is_(False))
    ).one()

    def _serialize_billing_rows(rows):
        return [
            {
                'id': r.id,
                'client_name': r.client_name,
                'amount': float(r.amount),
                'due_date': r.due_date.isoformat(),
                'days_until': (r.due_date - today).days,
            }
            for r in rows
        ]

    return {
        'clients': {
            **clientes,
            # Denominador explícito: todos os estados, inclusive suspensos.
            'total': sum(clientes.values()),
            'new_this_month': new_this,
            'new_prev_month': new_prev,
        },
        'vehicles': {
            'total': vehicles_total,
            'with_tracker': vehicles_with_tracker,
            'without_tracker': max(vehicles_total - vehicles_with_tracker, 0),
        },
        'trackers': dict(zip(('installed', 'stock', 'maintenance'), tracker_counts)),
        'service_orders': dict(zip(('open', 'in_progress', 'completed'), order_counts)),
        'finance': _finance() if ver_financeiro else None,
        # Vencem de hoje a hoje+7, mais próximos primeiro (máx. 5).
        'upcoming_billings': _serialize_billing_rows(upcoming_rows),
        # Já vencidas em aberto, mais antigas primeiro (máx. 5).
        'overdue_billings': _serialize_billing_rows(overdue_rows),
        'reference_date': today.isoformat(),
    }
