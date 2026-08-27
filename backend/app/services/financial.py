from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.timezone import hoje
from app.models.billing import Billing
from app.models.billing_charge_item import BillingChargeItem
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.models.plan import Plan
from app.models.service_product import ServiceProduct


def contract_payer_client_id(db: Session, contract: Contract) -> int:
    """Responsável financeiro efetivo, validado, para novas cobranças.

    O ID é congelado no Billing. Assim boleto e NFS-e não mudam de tomador se
    alguém editar o interveniente do contrato depois que o título foi emitido.
    """
    payer_id = contract.interveniente_client_id or contract.client_id
    payer = db.get(Client, payer_id)
    if not payer or payer.is_deleted:
        raise ValueError(
            f'Responsável financeiro #{payer_id} do contrato #{contract.id} não está disponível.'
        )
    return payer.id


def charge_item_payer_client_id(db: Session, item: ClientChargeItem) -> int:
    if not item.contract_id:
        return item.client_id
    contract = db.get(Contract, item.contract_id)
    if not contract or contract.is_deleted:
        raise ValueError(f'Contrato #{item.contract_id} do serviço não está disponível.')
    if contract.client_id != item.client_id:
        raise ValueError(
            f'Serviço #{item.id} e contrato #{contract.id} pertencem a clientes diferentes.'
        )
    return contract_payer_client_id(db, contract)


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
    Fonte ÚNICA do cálculo — tela, mensagens e integrações usam este valor.

    Calculado inteiramente em Decimal — o resultado sai do backend em
    float apenas na fronteira de serialização (contrato da API), não durante
    a conta. Fazer a multiplicação em float faz meio-centavo exato (ex.:
    1,50 × 1,03 = 1,545) cair do lado errado do arredondamento por causa da
    representação binária, divergindo do ROUND_HALF_UP usado no resto do
    serviço (parcelas, pró-rata)."""
    referencia = referencia or hoje()
    dias = (referencia - due_date).days
    if dias <= 0:
        return None
    meses = -(-dias // 30)  # ceil
    valor = Decimal(str(amount))
    atualizado = valor + valor * Decimal('0.02') + valor * Decimal('0.01') * meses
    return decimal_to_float(_quantize_amount(atualizado))


def refresh_overdue_statuses(db: Session, *, commit: bool = True) -> None:
    """Reclassifica pendente↔vencida via UPDATE no banco (sem carregar a tabela).

    ``commit=False`` para quem já está dentro de uma transação maior: comitar
    aqui encerraria a transação do chamador e, com ela, qualquer
    ``pg_advisory_xact_lock`` que ele tenha tomado.
    """
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
    if commit:
        db.commit()
    else:
        db.flush()


def mark_delinquent_clients(db: Session) -> dict:
    """
    Atualiza status dos clientes com base em cobranças vencidas:
    - ATIVO com cobranças vencidas → INADIMPLENTE
    - INADIMPLENTE sem cobranças vencidas → ATIVO

    Retorna um resumo das alterações realizadas.
    """
    from sqlalchemy import func, select as sa_select
    from app.models.client import Client
    from app.models.enums import ClientStatus

    # Atualiza billings primeiro
    refresh_overdue_statuses(db)

    # Clientes com cobranças vencidas
    overdue_client_ids: set[int] = set(
        db.scalars(
            sa_select(func.coalesce(Billing.payer_client_id, Billing.client_id))
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
        payer_client_id=contract_payer_client_id(db, contract),
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
            payer_client_id=contract_payer_client_id(db, contract),
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
            existing.payer_client_id = contract_payer_client_id(db, contract)
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
            payer_client_id=contract_payer_client_id(db, contract),
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


def generate_item_billings(
    db: Session, item: ClientChargeItem, force: bool = False, *, commit: bool = True,
) -> list[Billing]:
    """Gera as parcelas de um item de cobrança.

    ``commit=False`` para uso dentro de uma transação maior (o fechamento
    mensal): quem orquestra é que decide quando confirmar, senão um erro
    posterior deixa o fechamento gravado pela metade.
    """
    total_amount = _quantize_amount(item.total_amount)
    installments = max(int(item.installment_count or 1), 1)
    base_amount = (total_amount / installments).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    remainder = total_amount - (base_amount * installments)
    created: list[Billing] = []
    payer_client_id = charge_item_payer_client_id(db, item)

    for index in range(installments):
        due_date = normalize_due_date(item.start_date, index, item.start_date.day if item.start_date.day <= 28 else 28, 1)
        amount = base_amount + (remainder if index == installments - 1 else Decimal('0.00'))
        existing = (
            db.query(Billing)
            .filter(
                Billing.is_deleted == False,
                Billing.item_id == item.id,
                Billing.installment_number == index + 1,
                Billing.status != BillingStatus.CANCELED,
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
            existing.payer_client_id = payer_client_id
            existing.contract_id = item.contract_id
            existing.vehicle_id = item.vehicle_id
            existing.tracker_id = getattr(item, 'tracker_id', None)
            created.append(existing)
            continue

        billing = Billing(
            contract_id=item.contract_id,
            client_id=item.client_id,
            payer_client_id=payer_client_id,
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

    # Emitir não significa receber. O item sai da fila de geração, mas só vira
    # ``concluido`` quando todas as cobranças efetivas forem pagas.
    item.active = False
    item.status = 'faturado'
    item.completed_at = None

    if commit:
        db.commit()
        for row in created:
            db.refresh(row)
    else:
        # flush() dá id às linhas novas sem encerrar a transação do chamador.
        db.flush()
    refresh_overdue_statuses(db, commit=commit)
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
    refresh_charge_items_for_billing(db, billing, completion_date=payment_date, commit=False)
    db.commit()
    db.refresh(billing)
    return billing


def associate_billing_charge_item(
    db: Session,
    billing: Billing,
    item: ClientChargeItem,
    amount: Decimal | float,
) -> BillingChargeItem:
    """Associa um serviço a uma cobrança combinada sem encerrar a transação."""
    existing = db.scalar(
        select(BillingChargeItem).where(
            BillingChargeItem.billing_id == billing.id,
            BillingChargeItem.item_id == item.id,
        )
    )
    if existing:
        return existing
    link = BillingChargeItem(
        billing_id=billing.id,
        item_id=item.id,
        amount=_quantize_amount(amount),
    )
    db.add(link)
    return link


def charge_item_ids_for_billing(db: Session, billing: Billing) -> set[int]:
    item_ids = set(
        db.scalars(
            select(BillingChargeItem.item_id).where(
                BillingChargeItem.billing_id == billing.id,
            )
        ).all()
    )
    if billing.item_id:
        item_ids.add(billing.item_id)
    return item_ids


def billing_ids_for_charge_item(db: Session, item_id: int) -> list[int]:
    associated = select(BillingChargeItem.billing_id).where(
        BillingChargeItem.item_id == item_id,
    )
    return list(
        db.scalars(
            select(Billing.id)
            .where(
                Billing.is_deleted.is_(False),
                or_(Billing.item_id == item_id, Billing.id.in_(associated)),
            )
            .order_by(Billing.id.asc())
        ).all()
    )


def effective_charge_item_billings(db: Session, item_id: int) -> list[Billing]:
    associated = select(BillingChargeItem.billing_id).where(
        BillingChargeItem.item_id == item_id,
    )
    return list(
        db.scalars(
            select(Billing).where(
                Billing.is_deleted.is_(False),
                Billing.status != BillingStatus.CANCELED,
                or_(Billing.item_id == item_id, Billing.id.in_(associated)),
            )
        ).all()
    )


def charge_item_effective_billing_count(db: Session, item_id: int) -> int:
    return len(effective_charge_item_billings(db, item_id))


def refresh_charge_item_state(
    db: Session,
    item_id: int,
    *,
    completion_date: date | None = None,
) -> None:
    """Deriva o estado do item das cobranças, sem confundir emissão com pagamento."""
    item = db.get(ClientChargeItem, item_id)
    if not item or item.is_deleted:
        return

    billings = effective_charge_item_billings(db, item_id)
    abertas = [
        billing for billing in billings
        if billing.status in (BillingStatus.PENDING, BillingStatus.OVERDUE)
    ]
    pagas = [billing for billing in billings if billing.status == BillingStatus.PAID]
    effective_ids = [billing.id for billing in billings]
    embedded = bool(effective_ids) and db.scalar(
        select(BillingChargeItem.id)
        .where(
            BillingChargeItem.item_id == item_id,
            BillingChargeItem.billing_id.in_(effective_ids),
        )
        .limit(1)
    ) is not None

    if abertas:
        item.active = False
        item.status = 'faturado'
        item.completed_at = None
    elif pagas and (embedded or item.remove_after_payment):
        item.active = False
        item.status = 'concluido'
        paid_dates = [billing.payment_date for billing in pagas if billing.payment_date]
        item.completed_at = completion_date or (max(paid_dates) if paid_dates else hoje())
    elif pagas:
        # Mantém o histórico como faturado sem recolocá-lo na fila de cobrança.
        item.active = False
        item.status = 'faturado'
        item.completed_at = None
    else:
        # Todas as cobranças foram canceladas/removidas: o serviço precisa voltar
        # à fila, senão o cancelamento faria a receita desaparecer definitivamente.
        item.active = True
        item.status = 'ativo'
        item.completed_at = None


def refresh_charge_items_for_billing(
    db: Session,
    billing: Billing,
    *,
    completion_date: date | None = None,
    commit: bool = True,
) -> None:
    for item_id in charge_item_ids_for_billing(db, billing):
        refresh_charge_item_state(db, item_id, completion_date=completion_date)
    if commit:
        db.commit()
    else:
        db.flush()


def transfer_charge_items_to_billing(
    db: Session,
    source_billings: list[Billing],
    target: Billing,
) -> None:
    """Preserva os itens ao unificar cobranças em um novo título."""
    amounts_by_item: dict[int, Decimal] = {}
    for source in source_billings:
        links = list(db.scalars(
            select(BillingChargeItem).where(
                BillingChargeItem.billing_id == source.id,
            )
        ).all())
        linked_ids = {link.item_id for link in links}
        for link in links:
            amounts_by_item[link.item_id] = (
                amounts_by_item.get(link.item_id, Decimal('0.00'))
                + Decimal(str(link.amount))
            )
        if source.item_id and source.item_id not in linked_ids:
            amounts_by_item[source.item_id] = (
                amounts_by_item.get(source.item_id, Decimal('0.00'))
                + Decimal(str(source.amount))
            )

    for item_id, amount in amounts_by_item.items():
        item = db.get(ClientChargeItem, item_id)
        if item:
            associate_billing_charge_item(db, target, item, amount)

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
