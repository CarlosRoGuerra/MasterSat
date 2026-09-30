"""
Relatórios financeiros.

GET /reports/revenue          → Receita por período (mês/trimestre/ano)
GET /reports/delinquents      → Clientes inadimplentes com detalhamento
GET /reports/client-statement → Extrato completo de um cliente
GET /reports/summary          → Resumo executivo (KPIs avançados)
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, extract, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.billing import RECURRING_BILLING_TYPES, Billing
from app.models.billing_adjustment import BillingAdjustment
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

DEFINICOES_RECEITA = {
    'total_emitido': 'Valor das cobranças não canceladas, agrupado pela base escolhida (vencimento ou competência).',
    'total_recebido': 'Valor pago das cobranças do mês na mesma base do emitido (quanto do emitido já entrou).',
    'total_aberto': 'Cobranças pendentes/vencidas do mês, na mesma base do emitido.',
    'emitido_recorrente': 'Parte do emitido de mensalidade, pró-rata, 1ª mensalidade e carnê.',
    'emitido_avulso': 'Parte do emitido de serviços, taxas, negociações e saldos.',
    'total_recebido_caixa': 'Valor pago com data de pagamento no mês (regime de caixa), qualquer vencimento.',
    'encargos_caixa': 'Parte do caixa que é multa/juros ou crédito a favor do cliente (ajustes registrados).',
    'recebido_principal_caixa': 'Caixa menos encargos e créditos: quanto do principal das cobranças entrou.',
    'descontos_caixa': 'Abatimentos concedidos nos recebimentos do mês (não entram no caixa).',
}


def _caixa_por_mes(db: Session, date_from: date, date_to: date) -> dict[tuple[int, int], dict]:
    """Regime de caixa: pago no mês, com encargos/descontos do livro de ajustes."""
    pagos = (
        db.query(
            extract('year', Billing.payment_date).label('ano'),
            extract('month', Billing.payment_date).label('mes'),
            func.sum(func.coalesce(Billing.paid_amount, Billing.amount)).label('recebido'),
        )
        .filter(
            Billing.is_deleted.is_(False),
            Billing.status == BillingStatus.PAID,
            Billing.payment_date >= date_from,
            Billing.payment_date <= date_to,
        )
        .group_by('ano', 'mes')
        .all()
    )
    ajustes = (
        db.query(
            extract('year', Billing.payment_date).label('ano'),
            extract('month', Billing.payment_date).label('mes'),
            BillingAdjustment.kind,
            func.sum(BillingAdjustment.amount).label('valor'),
        )
        .join(Billing, Billing.id == BillingAdjustment.billing_id)
        .filter(
            Billing.is_deleted.is_(False),
            Billing.status == BillingStatus.PAID,
            Billing.payment_date >= date_from,
            Billing.payment_date <= date_to,
            BillingAdjustment.reversed_at.is_(None),
            BillingAdjustment.kind.in_(('encargos', 'credito', 'desconto')),
        )
        .group_by('ano', 'mes', BillingAdjustment.kind)
        .all()
    )
    caixa: dict[tuple[int, int], dict] = {}
    for r in pagos:
        caixa[(int(r.ano), int(r.mes))] = {
            'recebido': Decimal(str(r.recebido or 0)), 'encargos': Decimal('0'), 'descontos': Decimal('0'),
        }
    for r in ajustes:
        chave = (int(r.ano), int(r.mes))
        alvo = caixa.setdefault(chave, {'recebido': Decimal('0'), 'encargos': Decimal('0'), 'descontos': Decimal('0')})
        campo = 'descontos' if r.kind == 'desconto' else 'encargos'
        alvo[campo] += Decimal(str(r.valor or 0))
    return caixa


@router.get('/revenue')
def revenue_report(
    year: int = Query(default=None),
    date_from: date | None = Query(default=None, description='Início do período (inclusivo)'),
    date_to: date | None = Query(default=None, description='Fim do período (inclusivo)'),
    base: str = Query(
        default='vencimento', pattern='^(vencimento|competencia)$',
        description='Mês do emitido/recebido/aberto: vencimento (padrão, comportamento anterior) '
                    'ou competência (período cobrado).',
    ),
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
    # Base temporal do emitido/recebido/aberto, escolhida explicitamente
    # (PROD-01). Competência nula (rótulo legado fora de formato) cai no
    # vencimento — o dicionário de métricas documenta.
    referencia = Billing.due_date if base == 'vencimento' else func.coalesce(Billing.competencia, Billing.due_date)
    recorrente = Billing.billing_type.in_(RECURRING_BILLING_TYPES)

    results = (
        db.query(
            extract('year', referencia).label('ano'),
            extract('month', referencia).label('mes'),
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
            func.sum(case((recorrente, Billing.amount), else_=0)).label('emitido_recorrente'),
        )
        .filter(
            Billing.is_deleted.is_(False),
            nao_cancelada,
            referencia >= date_from,
            referencia <= date_to,
        )
        .group_by('ano', 'mes')
        .order_by('ano', 'mes')
        .all()
    )
    caixa = _caixa_por_mes(db, date_from, date_to)

    zero = Decimal('0')
    meses: dict[tuple[int, int], dict] = {}
    for r in results:
        emitido = Decimal(str(r.total_emitido or 0))
        emitido_recorrente = Decimal(str(r.emitido_recorrente or 0))
        meses[(int(r.ano), int(r.mes))] = {
            'total_cobrancas': r.total_cobrancas,
            'total_emitido': emitido,
            'total_recebido': Decimal(str(r.total_recebido or 0)),
            'total_aberto': Decimal(str(r.total_aberto or 0)),
            'emitido_recorrente': emitido_recorrente,
            'emitido_avulso': emitido - emitido_recorrente,
        }
    for chave in caixa:
        meses.setdefault(chave, {
            'total_cobrancas': 0, 'total_emitido': zero, 'total_recebido': zero, 'total_aberto': zero,
            'emitido_recorrente': zero, 'emitido_avulso': zero,
        })

    months_data = []
    for (ano, mes) in sorted(meses):
        m = meses[(ano, mes)]
        c = caixa.get((ano, mes), {'recebido': zero, 'encargos': zero, 'descontos': zero})
        months_data.append({
            'ano': ano,
            'mes': mes,
            'label': f'{mes:02d}/{ano}',
            'total_cobrancas': m['total_cobrancas'],
            'total_emitido': float(m['total_emitido']),
            'total_recebido': float(m['total_recebido']),
            'total_aberto': float(m['total_aberto']),
            'emitido_recorrente': float(m['emitido_recorrente']),
            'emitido_avulso': float(m['emitido_avulso']),
            'total_recebido_caixa': float(c['recebido']),
            'encargos_caixa': float(c['encargos']),
            'recebido_principal_caixa': float(c['recebido'] - c['encargos']),
            'descontos_caixa': float(c['descontos']),
        })

    def _soma(campo: str) -> float:
        return float(sum((Decimal(str(m[campo])) for m in months_data), Decimal('0')))

    total_emitido = _soma('total_emitido')
    total_recebido = _soma('total_recebido')

    return {
        'ano': year,
        'periodo': {'de': date_from.isoformat(), 'ate': date_to.isoformat()},
        'base': base,
        'definicoes': DEFINICOES_RECEITA,
        'meses': months_data,
        'totais': {
            'total_emitido': total_emitido,
            'total_recebido': total_recebido,
            'total_aberto': _soma('total_aberto'),
            'taxa_recebimento': round((total_recebido / total_emitido * 100) if total_emitido else 0, 1),
            'emitido_recorrente': _soma('emitido_recorrente'),
            'emitido_avulso': _soma('emitido_avulso'),
            'total_recebido_caixa': _soma('total_recebido_caixa'),
            'encargos_caixa': _soma('encargos_caixa'),
            'recebido_principal_caixa': _soma('recebido_principal_caixa'),
            'descontos_caixa': _soma('descontos_caixa'),
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
        .join(
            Billing,
            func.coalesce(Billing.payer_client_id, Billing.client_id) == Client.id,
        )
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
    """Extrato do cliente (PROD-01/FIN-10).

    Lista as cobranças em que o cliente é o ATENDIDO (dono do contrato) e as
    em que é o RESPONSÁVEL FINANCEIRO (pagador/interveniente); cada linha diz
    o ``papel``. Dois resumos, cada um sobre o seu papel — ``resumo``
    (atendido, formato anterior) e ``resumo_responsavel_financeiro``.
    Cancelada aparece na lista, mas não soma em cobrado/aberto: não é dívida.
    Cliente removido continua tendo extrato (histórico financeiro).
    """
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail='Cliente não encontrado')

    pagador = func.coalesce(Billing.payer_client_id, Billing.client_id)
    query = (
        db.query(Billing)
        .filter(
            or_(Billing.client_id == client_id, pagador == client_id),
            Billing.is_deleted.is_(False),
        )
    )
    if date_from:
        query = query.filter(Billing.due_date >= date_from)
    if date_to:
        query = query.filter(Billing.due_date <= date_to)
    billings = query.order_by(Billing.due_date.desc(), Billing.id.desc()).all()

    vehicle_ids = {b.vehicle_id for b in billings if b.vehicle_id}
    vehicle_map = {
        v.id: v.plate for v in db.query(Vehicle).filter(Vehicle.id.in_(vehicle_ids)).all()
    } if vehicle_ids else {}

    def _papel(b: Billing) -> str:
        atendido = b.client_id == client_id
        paga = (b.payer_client_id or b.client_id) == client_id
        return 'ambos' if atendido and paga else ('atendido' if atendido else 'responsavel_financeiro')

    def _resumo(linhas: list[Billing]) -> dict:
        efetivas = [b for b in linhas if b.status != BillingStatus.CANCELED]
        return {
            'total_cobrado': float(sum((Decimal(str(b.amount)) for b in efetivas), Decimal('0'))),
            'total_pago': float(sum(
                (Decimal(str(b.paid_amount if b.paid_amount is not None else b.amount))
                 for b in efetivas if b.status == BillingStatus.PAID), Decimal('0'),
            )),
            'total_aberto': float(sum(
                (Decimal(str(b.amount)) for b in efetivas
                 if b.status in (BillingStatus.PENDING, BillingStatus.OVERDUE)), Decimal('0'),
            )),
            'qtd_cobrancas': len(linhas),
            'qtd_canceladas': len(linhas) - len(efetivas),
        }

    como_atendido = [b for b in billings if b.client_id == client_id]
    como_pagador = [b for b in billings if (b.payer_client_id or b.client_id) == client_id]

    return {
        'cliente': {
            'id': client.id,
            'nome': client.name,
            'cpf_cnpj': client.cpf_cnpj,
            'email': client.email,
            'status': client.status.value if hasattr(client.status, 'value') else str(client.status),
            'removido': bool(client.is_deleted),
        },
        'resumo': _resumo(como_atendido),
        'resumo_responsavel_financeiro': _resumo(como_pagador),
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
                'papel': _papel(b),
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
            Billing.status != BillingStatus.CANCELED,
            Billing.due_date >= (today - timedelta(days=180)),
        )
        .group_by('ano', 'mes')
        .order_by('ano', 'mes')
        .all()
    )

    # Top clientes inadimplentes
    top_delinquents = (
        db.query(Client.name, func.sum(Billing.amount).label('valor'))
        .join(
            Billing,
            func.coalesce(Billing.payer_client_id, Billing.client_id) == Client.id,
        )
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
        # receita_6_meses: pago das cobranças pelo mês de VENCIMENTO (mesma base
        # de /reports/revenue padrão), não caixa — ver dicionário de métricas.
        'bases': {'receita_6_meses': 'vencimento'},
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
