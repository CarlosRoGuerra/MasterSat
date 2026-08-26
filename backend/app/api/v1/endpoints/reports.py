"""
Relatórios financeiros.

GET /reports/revenue          → Receita por período (mês/trimestre/ano)
GET /reports/delinquents      → Clientes inadimplentes com detalhamento
GET /reports/client-statement → Extrato completo de um cliente
GET /reports/summary          → Resumo executivo (KPIs avançados)
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, extract, func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus, UserRole
from app.models.plan import Plan
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle

router = APIRouter()

VIEW_ROLES = (UserRole.ADMIN, UserRole.FINANCIAL)


# ---------------------------------------------------------------------------
# Receita por período
# ---------------------------------------------------------------------------

@router.get('/revenue')
def revenue_report(
    year: int = Query(default=None),
    date_from: date | None = Query(default=None, description='Início do período (inclusivo)'),
    date_to: date | None = Query(default=None, description='Fim do período (inclusivo)'),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    """
    Receita mês a mês: emitido, recebido e em aberto.

    Aceita um intervalo livre (``date_from``/``date_to``), que pode cruzar a
    virada do ano. Antes só existia o filtro por ``year``, e a tela contornava
    isso pedindo o ano da data inicial e descartando o resto no cliente — um
    período como dez/2025→jan/2026 perdia janeiro inteiro.

    Cobranças CANCELADAS ficam de fora de todos os totais: elas não são
    receita. Incluí-las inflava o emitido e distorcia a taxa de recebimento —
    especialmente porque a consolidação em boleto único cancela as cobranças
    originais, de modo que cada cliente com boleto único era contado duas
    vezes (as parcelas canceladas + o boleto que as substituiu).
    """
    today = date.today()
    if date_from is None and date_to is None:
        year = year or today.year
        date_from = date(year, 1, 1)
        date_to = date(year, 12, 31)
    else:
        if date_from is None:
            date_from = date(date_to.year, 1, 1)
        if date_to is None:
            date_to = date(date_from.year, 12, 31)
        if date_from > date_to:
            raise HTTPException(status_code=422, detail='date_from não pode ser posterior a date_to.')

    nao_cancelada = Billing.status != BillingStatus.CANCELED

    results = (
        db.query(
            extract('year', Billing.due_date).label('ano'),
            extract('month', Billing.due_date).label('mes'),
            func.count(Billing.id).label('total_cobrancas'),
            func.sum(Billing.amount).label('total_emitido'),
            func.sum(
                case((Billing.status == BillingStatus.PAID, func.coalesce(Billing.paid_amount, Billing.amount)), else_=0)
            ).label('total_recebido'),
            func.sum(
                case(
                    (Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]), Billing.amount),
                    else_=0,
                )
            ).label('total_aberto'),
        )
        .filter(
            Billing.is_deleted.is_(False),
            nao_cancelada,
            Billing.due_date >= date_from,
            Billing.due_date <= date_to,
        )
        .group_by('ano', 'mes')
        .order_by('ano', 'mes')
        .all()
    )

    months_data = []
    for r in results:
        months_data.append({
            'ano': int(r.ano),
            'mes': int(r.mes),
            'label': f'{int(r.mes):02d}/{int(r.ano)}',
            'total_cobrancas': r.total_cobrancas,
            'total_emitido': float(r.total_emitido or 0),
            'total_recebido': float(r.total_recebido or 0),
            'total_aberto': float(r.total_aberto or 0),
        })

    # Totais do ano
    total_emitido = sum(m['total_emitido'] for m in months_data)
    total_recebido = sum(m['total_recebido'] for m in months_data)
    total_aberto = sum(m['total_aberto'] for m in months_data)

    return {
        'ano': year,
        'periodo': {'de': date_from.isoformat(), 'ate': date_to.isoformat()},
        'meses': months_data,
        'totais': {
            'total_emitido': total_emitido,
            'total_recebido': total_recebido,
            'total_aberto': total_aberto,
            'taxa_recebimento': round((total_recebido / total_emitido * 100) if total_emitido else 0, 1),
        },
    }


# ---------------------------------------------------------------------------
# Relatório de inadimplentes
# ---------------------------------------------------------------------------

@router.get('/delinquents')
def delinquents_report(
    min_amount: float = Query(default=0),
    min_days: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    today = date.today()
    cutoff = today - timedelta(days=min_days)

    results = (
        db.query(
            Client.id,
            Client.name,
            Client.cpf_cnpj,
            Client.email,
            Client.phone,
            Client.status,
            func.count(Billing.id).label('qtd_vencidas'),
            func.sum(Billing.amount).label('valor_total'),
            func.min(Billing.due_date).label('mais_antiga'),
            func.max(Billing.due_date).label('mais_recente'),
        )
        .join(Billing, Billing.client_id == Client.id)
        .filter(
            Client.is_deleted.is_(False),
            Billing.is_deleted.is_(False),
            Billing.status == BillingStatus.OVERDUE,
            Billing.due_date <= cutoff,
        )
        .group_by(
            Client.id, Client.name, Client.cpf_cnpj,
            Client.email, Client.phone, Client.status,
        )
        .having(func.sum(Billing.amount) >= min_amount)
        .order_by(func.sum(Billing.amount).desc())
        .all()
    )

    data = [
        {
            'client_id': r.id,
            'nome': r.name,
            'cpf_cnpj': r.cpf_cnpj,
            'email': r.email,
            'phone': r.phone,
            'status_atual': r.status.value if hasattr(r.status, 'value') else str(r.status),
            'qtd_cobrancas_vencidas': r.qtd_vencidas,
            'valor_total_vencido': float(r.valor_total or 0),
            'vencimento_mais_antigo': str(r.mais_antiga) if r.mais_antiga else None,
            'vencimento_mais_recente': str(r.mais_recente) if r.mais_recente else None,
            'dias_atraso_max': (today - r.mais_antiga).days if r.mais_antiga else 0,
        }
        for r in results
    ]

    return {
        'total_clientes': len(data),
        'valor_total_inadimplencia': sum(d['valor_total_vencido'] for d in data),
        'clientes': data,
    }


# ---------------------------------------------------------------------------
# Extrato do cliente
# ---------------------------------------------------------------------------

@router.get('/client-statement/{client_id}')
def client_statement(
    client_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    client = db.get(Client, client_id)
    if not client or client.is_deleted:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail='Cliente não encontrado')

    query = (
        db.query(Billing)
        .filter(
            Billing.client_id == client_id,
            Billing.is_deleted.is_(False),
        )
    )
    if date_from:
        query = query.filter(Billing.due_date >= date_from)
    if date_to:
        query = query.filter(Billing.due_date <= date_to)
    billings = query.order_by(Billing.due_date.desc()).all()

    vehicle_map = {v.id: v.plate for v in db.query(Vehicle).filter(Vehicle.is_deleted.is_(False)).all()}

    total_cobrado = sum(float(b.amount) for b in billings)
    total_pago = sum(float(b.paid_amount or 0) for b in billings if b.status == BillingStatus.PAID)
    total_aberto = sum(float(b.amount) for b in billings if b.status in (BillingStatus.PENDING, BillingStatus.OVERDUE))

    return {
        'cliente': {
            'id': client.id,
            'nome': client.name,
            'cpf_cnpj': client.cpf_cnpj,
            'email': client.email,
            'status': client.status.value if hasattr(client.status, 'value') else str(client.status),
        },
        'resumo': {
            'total_cobrado': total_cobrado,
            'total_pago': total_pago,
            'total_aberto': total_aberto,
            'qtd_cobrancas': len(billings),
        },
        'cobrancas': [
            {
                'id': b.id,
                'titulo': b.title,
                'tipo': b.billing_type,
                'vencimento': str(b.due_date),
                'valor': float(b.amount),
                'valor_pago': float(b.paid_amount or 0),
                'status': b.status.value if hasattr(b.status, 'value') else str(b.status),
                'pagamento': str(b.payment_date) if b.payment_date else None,
                'periodo': b.period_label,
                'veiculo': vehicle_map.get(b.vehicle_id, '') if b.vehicle_id else '',
                'parcela': f'{b.installment_number}/{b.installment_total}' if b.installment_number else None,
            }
            for b in billings
        ],
    }


# ---------------------------------------------------------------------------
# Resumo executivo avançado
# ---------------------------------------------------------------------------

@router.get('/summary')
def executive_summary(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*VIEW_ROLES)),
):
    today = date.today()
    first_of_month = today.replace(day=1)

    # Contratos ativos por plano
    contracts_by_plan = (
        db.query(Plan.name, func.count(Contract.id).label('qtd'))
        .join(Contract, Contract.plan_id == Plan.id)
        .filter(Contract.is_deleted.is_(False), Contract.status == 'ativo', Plan.is_deleted.is_(False))
        .group_by(Plan.name)
        .order_by(func.count(Contract.id).desc())
        .all()
    )

    # Receita dos últimos 6 meses
    last_6 = (
        db.query(
            extract('year', Billing.due_date).label('ano'),
            extract('month', Billing.due_date).label('mes'),
            func.sum(
                case((Billing.status == BillingStatus.PAID, func.coalesce(Billing.paid_amount, Billing.amount)), else_=0)
            ).label('recebido'),
        )
        .filter(
            Billing.is_deleted.is_(False),
            Billing.due_date >= (today - timedelta(days=180)),
        )
        .group_by('ano', 'mes')
        .order_by('ano', 'mes')
        .all()
    )

    # Top clientes inadimplentes
    top_delinquents = (
        db.query(Client.name, func.sum(Billing.amount).label('valor'))
        .join(Billing, Billing.client_id == Client.id)
        .filter(
            Client.is_deleted.is_(False),
            Billing.is_deleted.is_(False),
            Billing.status == BillingStatus.OVERDUE,
        )
        .group_by(Client.name)
        .order_by(func.sum(Billing.amount).desc())
        .limit(5)
        .all()
    )

    return {
        'contratos_por_plano': [{'plano': r.name, 'contratos': r.qtd} for r in contracts_by_plan],
        'receita_6_meses': [
            {
                'label': f'{int(r.mes):02d}/{int(r.ano)}',
                'recebido': float(r.recebido or 0),
            }
            for r in last_6
        ],
        'top_inadimplentes': [
            {'cliente': r.name, 'valor': float(r.valor or 0)}
            for r in top_delinquents
        ],
    }
