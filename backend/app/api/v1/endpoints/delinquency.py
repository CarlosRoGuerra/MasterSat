"""
Gestão automática de inadimplência.

POST /delinquency/refresh  → Executa verificação e atualiza status dos clientes
GET  /delinquency/status   → Retorna quantos clientes estão inadimplentes agora
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.permissions import Capability, has_capability, roles_with
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.services.financial import mark_delinquent_clients

router = APIRouter()


@router.post('/refresh')
def run_delinquency_check(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL)),
):
    """
    Atualiza status de inadimplência de todos os clientes.
    Pode ser chamado manualmente pelo admin ou disparado automaticamente
    por qualquer operação financeira relevante.
    """
    result = mark_delinquent_clients(db)
    return {
        'message': 'Verificação de inadimplência concluída.',
        **result,
    }


@router.get('/status')
def delinquency_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*roles_with(Capability.REGISTRY_READ))),
):
    """Quantos clientes estão inadimplentes (o operacional usa para bloqueio
    e desinstalação). Quantidade e valor dos títulos vencidos são dado
    financeiro: para perfil sem FINANCIAL_READ voltam null (SEC-01)."""
    from sqlalchemy import func
    from app.models.billing import Billing
    from app.models.client import Client
    from app.models.enums import BillingStatus, ClientStatus

    ver_financeiro = has_capability(current_user.role, Capability.FINANCIAL_READ)
    overdue_billings = None if not ver_financeiro else (
        db.query(
            func.count(Billing.id).label('qtd'),
            func.sum(Billing.amount).label('valor'),
        )
        .join(
            Client,
            Client.id == func.coalesce(Billing.payer_client_id, Billing.client_id),
        )
        .filter(
            Billing.is_deleted.is_(False),
            Billing.status == BillingStatus.OVERDUE,
            Client.is_deleted.is_(False),
        )
        .first()
    )

    delinquent_clients = (
        db.query(func.count(Client.id))
        .filter(
            Client.is_deleted.is_(False),
            Client.status == ClientStatus.DELINQUENT,
        )
        .scalar()
        or 0
    )

    return {
        'clientes_inadimplentes': delinquent_clients,
        'cobrancas_vencidas': (overdue_billings.qtd or 0) if ver_financeiro else None,
        'valor_total_vencido': float(overdue_billings.valor or 0) if ver_financeiro else None,
    }
