from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from sqlalchemy.orm import Session

from app.core.timezone import hoje
from app.models.billing import Billing
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.models.plan import Plan
from app.models.service_product import ServiceProduct


def decimal_to_float(value: Decimal | float | int | None) -> float:
    if value is None:
        return 0.0
    return float(value)


def add_months(source_date: date, months: int) -> date:
    month = source_date.month - 1 + months
    year = source_date.year + month // 12
    month = month % 12 + 1
    day = min(source_date.day, monthrange(year, month)[1])
    return date(year, month, day)


def normalize_due_date(start_date: date, cycle: int, billing_day: int | None = None, interval_months: int = 1) -> date:
    base = add_months(start_date, cycle * interval_months)
    day = billing_day or start_date.day
    day = min(day, monthrange(base.year, base.month)[1])
    return date(base.year, base.month, day)


def period_label_for_date(reference: date, interval_months: int = 1) -> str:
    if interval_months == 12:
        return str(reference.year)
    if interval_months == 6:
        semester = 1 if reference.month <= 6 else 2
        return f'{reference.year} • S{semester}'
    if interval_months == 3:
        quarter = ((reference.month - 1) // 3) + 1
        return f'{reference.year} • T{quarter}'
    return reference.strftime('%m/%Y')


def plan_title(plan) -> str:
    """Título da cobrança a partir do plano, sem duplicar a palavra 'Plano'
    ("Plano Plano TESTE" quando o nome do plano já começa com 'Plano')."""
    name = (getattr(plan, 'name', '') or '').strip()
    return name if name.lower().startswith('plano') else f'Plano {name}'


def valor_com_juros(amount, due_date: date, referencia: date | None = None) -> float | None:
    """Valor atualizado de cobrança em atraso: multa 2% + juros de 1% ao mês
    ou fração (cláusula 4.3 do contrato). None se não está em atraso.
    Fonte ÚNICA do cálculo — tela, mensagens e integrações usam este valor."""
    referencia = referencia or hoje()
    dias = (referencia - due_date).days
    if dias <= 0:
        return None
    meses = -(-dias // 30)  # ceil
    valor = float(amount)
    return round(valor * 1.02 + valor * 0.01 * meses, 2)


def refresh_overdue_statuses(db: Session) -> None:
    """Reclassifica pendente↔vencida via UPDATE no banco (sem carregar a tabela)."""
    today = hoje()
    db.query(Billing).filter(
        Billing.is_deleted == False,
        Billing.status == BillingStatus.PENDING,
        Billing.due_date < today,
    ).update({Billing.status: BillingStatus.OVERDUE}, synchronize_session=False)
    db.query(Billing).filter(
        Billing.is_deleted == False,
        Billing.status == BillingStatus.OVERDUE,
        Billing.due_date >= today,
    ).update({Billing.status: BillingStatus.PENDING}, synchronize_session=False)
    db.commit()


def mark_delinquent_clients(db: Session) -> dict:
    """
    Atualiza status dos clientes com base em cobranças vencidas:
    - ATIVO com cobranças vencidas → INADIMPLENTE
    - INADIMPLENTE sem cobranças vencidas → ATIVO

    Retorna um resumo das alterações realizadas.
    """
    from sqlalchemy import select as sa_select
    from app.models.client import Client
    from app.models.enums import ClientStatus

    # Atualiza billings primeiro
    refresh_overdue_statuses(db)

    # Clientes com cobranças vencidas
    overdue_client_ids: set[int] = set(
        db.scalars(
            sa_select(Billing.client_id)
            .where(
                Billing.is_deleted.is_(False),
                Billing.status == BillingStatus.OVERDUE,
            )
            .distinct()
        ).all()
    )

    marked_delinquent = 0
    restored_active = 0

    clients = db.query(Client).filter(
        Client.is_deleted.is_(False),
        Client.status.in_([ClientStatus.ACTIVE, ClientStatus.DELINQUENT]),
    ).all()

    for client in clients:
        if client.id in overdue_client_ids and client.status == ClientStatus.ACTIVE:
            client.status = ClientStatus.DELINQUENT
            marked_delinquent += 1
        elif client.id not in overdue_client_ids and client.status == ClientStatus.DELINQUENT:
            client.status = ClientStatus.ACTIVE
            restored_active += 1

    if marked_delinquent or restored_active:
        db.commit()

    return {
        'marcados_inadimplentes': marked_delinquent,
        'restaurados_ativos': restored_active,
        'total_inadimplentes_agora': len(overdue_client_ids),
    }


def generate_receipt_number(billing_id: int) -> str:
    now = datetime.now().strftime('%Y%m%d')
    return f'RCB-{now}-{billing_id:05d}'


def _quantize_amount(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def generate_prorated_first_billing(
    db: Session,
    contract: Contract,
    plan: Plan,
    install_date: date,
    installation_fee: float = 0.0,
) -> list[Billing]:
    """
    Gera cobranças separadas para o período proporcional e taxa de instalação.

    Lógica:
      - Calcula os dias restantes do mês de instalação (incluindo o próprio dia)
      - Cria 1 billing de pró-rata: título = 'Mensalidade — N dias — R$ X,XX'
      - Se installation_fee > 0: cria billing separado para a taxa
      - A fatura mensal recorrente começa somente a partir do mês seguinte
    """
    days_in_month = monthrange(install_date.year, install_date.month)[1]
    remaining_days = days_in_month - install_date.day + 1

    plan_price = Decimal(str(plan.price))
    prorated = (plan_price * Decimal(remaining_days) / Decimal(days_in_month)).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )

    # Vencimento no billing_day do contrato, dentro do mês de instalação
    billing_day = contract.billing_day or install_date.day
    billing_day = min(billing_day, days_in_month)
    due_date = date(install_date.year, install_date.month, billing_day)
    if due_date < install_date:
        due_date = add_months(due_date, 1)

    period_label = install_date.strftime('%m/%Y')
    created: list[Billing] = []

    prorata_billing = Billing(
        contract_id=contract.id,
        client_id=contract.client_id,
        vehicle_id=getattr(contract, 'vehicle_id', None),
        tracker_id=getattr(contract, 'tracker_id', None),
        amount=prorated,
        due_date=due_date,
        status=BillingStatus.PENDING if due_date >= hoje() else BillingStatus.OVERDUE,
        period_label=period_label,
        payment_method=contract.payment_method,
        notes=f'Pró-rata: {remaining_days} de {days_in_month} dias do mês',
        title=f'Mensalidade — {remaining_days} dias — R$ {float(prorated):.2f}',
        billing_type='prorata',
    )
    db.add(prorata_billing)
    created.append(prorata_billing)

    if installation_fee and installation_fee > 0:
        fee = _quantize_amount(installation_fee)
        fee_billing = Billing(
            contract_id=contract.id,
            client_id=contract.client_id,
            vehicle_id=getattr(contract, 'vehicle_id', None),
            tracker_id=getattr(contract, 'tracker_id', None),
            amount=fee,
            due_date=due_date,
            status=BillingStatus.PENDING if due_date >= hoje() else BillingStatus.OVERDUE,
            period_label=period_label,
            payment_method=contract.payment_method,
            notes='Taxa de instalação do rastreador',
            title='Taxa de instalação',
            billing_type='taxa_instalacao',
        )
        db.add(fee_billing)
        created.append(fee_billing)

    db.commit()
    for b in created:
        db.refresh(b)
    return created


def generate_monthly_billings(db: Session, contract: Contract, cycles: int = 12, force: bool = False, start_cycle: int = 0) -> list[Billing]:
    plan = db.get(Plan, contract.plan_id)
    if not plan:
        raise ValueError('Plano não encontrado para o contrato informado.')

    interval = max(int(getattr(plan, 'billing_interval_months', 1) or 1), 1)
    created: list[Billing] = []
    for cycle in range(start_cycle, start_cycle + cycles):
        due_date = normalize_due_date(contract.start_date, cycle, contract.billing_day, interval)
        if contract.end_date and due_date > contract.end_date:
            break

        period_label = period_label_for_date(due_date, interval)
        existing = (
            db.query(Billing)
            .filter(
                Billing.is_deleted == False,
                Billing.contract_id == contract.id,
                Billing.item_id.is_(None),
                Billing.period_label == period_label,
                Billing.billing_type == 'recorrente',
            )
            .first()
        )
        notes = f'Cobrança recorrente automática do plano {plan.name}'
        if existing and not force:
            continue
        if existing and force:
            existing.amount = plan.price
            existing.due_date = due_date
            existing.client_id = contract.client_id
            existing.period_label = period_label
            existing.title = plan_title(plan)
            existing.notes = notes
            existing.vehicle_id = getattr(contract, 'vehicle_id', None)
            existing.tracker_id = getattr(contract, 'tracker_id', None)
            created.append(existing)
            continue

        billing = Billing(
            contract_id=contract.id,
            client_id=contract.client_id,
            amount=plan.price,
            due_date=due_date,
            status=BillingStatus.PENDING if due_date >= hoje() else BillingStatus.OVERDUE,
            period_label=period_label,
            payment_method=contract.payment_method,
            notes=notes,
            vehicle_id=getattr(contract, 'vehicle_id', None),
            tracker_id=getattr(contract, 'tracker_id', None),
            title=plan_title(plan),
            billing_type='recorrente',
        )
        db.add(billing)
        created.append(billing)
    db.commit()
    for item in created:
        db.refresh(item)
    refresh_overdue_statuses(db)
    return created


def generate_item_billings(db: Session, item: ClientChargeItem, force: bool = False) -> list[Billing]:
    total_amount = _quantize_amount(item.total_amount)
    installments = max(int(item.installment_count or 1), 1)
    base_amount = (total_amount / installments).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    remainder = total_amount - (base_amount * installments)
    created: list[Billing] = []

    for index in range(installments):
        due_date = normalize_due_date(item.start_date, index, item.start_date.day if item.start_date.day <= 28 else 28, 1)
        amount = base_amount + (remainder if index == installments - 1 else Decimal('0.00'))
        existing = (
            db.query(Billing)
            .filter(
                Billing.is_deleted == False,
                Billing.item_id == item.id,
                Billing.installment_number == index + 1,
            )
            .first()
        )
        title = item.title if installments == 1 else f'{item.title} • parcela {index + 1}/{installments}'
        if existing and not force:
            continue
        if existing and force:
            existing.amount = amount
            existing.due_date = due_date
            existing.title = title
            existing.client_id = item.client_id
            existing.contract_id = item.contract_id
            existing.vehicle_id = item.vehicle_id
            existing.tracker_id = getattr(item, 'tracker_id', None)
            created.append(existing)
            continue

        billing = Billing(
            contract_id=item.contract_id,
            client_id=item.client_id,
            item_id=item.id,
            vehicle_id=item.vehicle_id,
            tracker_id=getattr(item, 'tracker_id', None),
            title=title,
            billing_type='item',
            installment_number=index + 1,
            installment_total=installments,
            amount=amount,
            due_date=due_date,
            status=BillingStatus.PENDING if due_date >= hoje() else BillingStatus.OVERDUE,
            period_label=due_date.strftime('%m/%Y'),
            notes=item.description,
        )
        db.add(billing)
        created.append(billing)

    db.commit()
    for row in created:
        db.refresh(row)
    refresh_overdue_statuses(db)
    return created


def marcar_billing_pago(
    db: Session,
    billing: Billing,
    *,
    payment_date: date,
    paid_amount: float | Decimal | None,
    payment_method: str = 'boleto',
    notes: str | None = None,
) -> Billing:
    """Marca uma cobrança como paga (status, data, valor, recibo).

    Reaproveitado pelo recebimento manual e pela baixa automática Ailos —
    mesma lógica do endpoint /billings/{id}/receive.
    """
    billing.status = BillingStatus.PAID
    billing.payment_date = payment_date
    billing.payment_method = payment_method
    if notes:
        billing.notes = notes
    billing.paid_amount = paid_amount if paid_amount else billing.amount
    if not billing.receipt_number:
        billing.receipt_number = generate_receipt_number(billing.id)
    db.commit()
    db.refresh(billing)
    mark_charge_item_if_settled(db, billing.item_id)
    return billing


def mark_charge_item_if_settled(db: Session, item_id: int | None) -> None:
    if not item_id:
        return
    item = db.get(ClientChargeItem, item_id)
    if not item or item.is_deleted:
        return
    open_items = db.query(Billing).filter(
        Billing.is_deleted == False,
        Billing.item_id == item.id,
        Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
    ).count()
    if open_items == 0 and item.remove_after_payment:
        item.active = False
        item.status = 'concluido'
        item.completed_at = hoje()
        db.commit()


def current_cycle_bounds(contract: Contract, plan: Plan, reference_date: date) -> tuple[date, date]:
    interval = max(int(getattr(plan, 'billing_interval_months', 1) or 1), 1)
    cycle_start = contract.start_date
    while True:
        next_start = add_months(cycle_start, interval)
        if next_start > reference_date:
            break
        cycle_start = next_start
    cycle_end = add_months(cycle_start, interval) - timedelta(days=1)
    return cycle_start, cycle_end


def prorated_amount(plan_price: Decimal | float, period_start: date, period_end: date, cutoff_end: date) -> Decimal:
    full = _quantize_amount(plan_price)
    total_days = max((period_end - period_start).days + 1, 1)
    used_days = max((cutoff_end - period_start).days + 1, 0)
    used_days = min(used_days, total_days)
    return (full * Decimal(used_days) / Decimal(total_days)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def period_bucket(reference: date, period: str) -> str:
    if period == 'annual':
        return str(reference.year)
    if period == 'quarterly':
        quarter = ((reference.month - 1) // 3) + 1
        return f'{reference.year} • T{quarter}'
    return reference.strftime('%m/%Y')


def sum_billing_amounts(items: Iterable[Billing]) -> float:
    total = 0.0
    for item in items:
        total += decimal_to_float(item.amount)
    return round(total, 2)
