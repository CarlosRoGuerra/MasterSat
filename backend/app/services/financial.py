from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.timezone import hoje
from app.models.ailos_boleto import AilosBoleto
from app.models.billing import RECURRING_BILLING_TYPES, Billing
from app.models.billing_change_log import BillingChangeLog
from app.models.billing_charge_item import BillingChargeItem
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.models.plan import Plan
from app.models.service_product import ServiceProduct
from app.services.competencia import competencia_do_rotulo


def lock_billings_for_update(db: Session, billing_ids: Iterable[int]) -> list[Billing]:
    """Lock billing rows in a stable order for state-dependent mutations.

    ``populate_existing`` is intentional: a caller may already have loaded a
    Billing in the identity map before asking for the lock.  Revalidation must
    see the version committed by the transaction that released the row lock.
    """
    ordered_ids = sorted(set(billing_ids))
    if not ordered_ids:
        return []
    statement = (
        select(Billing)
        .where(Billing.id.in_(ordered_ids))
        .order_by(Billing.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(db.scalars(statement).all())


def charge_item_ids_for_billings(db: Session, billings: Iterable[Billing]) -> list[int]:
    rows = list(billings)
    billing_ids = [billing.id for billing in rows if billing.id is not None]
    item_ids = {billing.item_id for billing in rows if billing.item_id is not None}
    if billing_ids:
        item_ids.update(db.scalars(
            select(BillingChargeItem.item_id).where(
                BillingChargeItem.billing_id.in_(billing_ids),
            )
        ).all())
    return sorted(item_ids)


def lock_charge_items_for_update(
    db: Session,
    item_ids: Iterable[int],
) -> list[ClientChargeItem]:
    """Serialize derived charge-item state updates in primary-key order."""
    ordered_ids = sorted(set(item_ids))
    if not ordered_ids:
        return []
    statement = (
        select(ClientChargeItem)
        .where(ClientChargeItem.id.in_(ordered_ids))
        .order_by(ClientChargeItem.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(db.scalars(statement).all())


def lock_charge_items_for_billings(
    db: Session,
    billings: Iterable[Billing],
) -> list[ClientChargeItem]:
    return lock_charge_items_for_update(db, charge_item_ids_for_billings(db, billings))


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


# Tipos de cobrança que "ocupam" um mês de um contrato: são a mensalidade
# daquele período, em qualquer forma que ela tenha assumido. Não inclui
# taxa_instalacao/taxa_desinstalacao/avulsa/item — essas são cobranças
# paralelas e podem coexistir com a mensalidade do mesmo período.
#
# Única fonte da verdade para "esse contrato já tem cobrança neste mês?" —
# usada tanto pelo fechamento mensal (não gerar por cima de um carnê) quanto
# pelo parcelamento manual (não gerar carnê por cima de um mês já fechado).
# As duas listas divergentes já causaram cobrança duplicada em produção.
# A tupla mora em app/models/billing.py (o índice único usa a mesma) e é
# reexportada aqui por compatibilidade com quem já a importa deste módulo.


def occupying_recurring_filter():
    """Condições para uma cobrança ocupar o mês do contrato — as mesmas do
    índice uq_billings_contract_competencia_recorrente.

    Cancelada continua ocupando (é o que impede recobrar um mês consolidado,
    negociado ou dispensado). Só sai quem foi removido ou teve a competência
    liberada explicitamente (release_billing_competencia).
    """
    return (
        Billing.is_deleted.is_(False),
        Billing.competencia_liberada.is_(False),
        Billing.billing_type.in_(RECURRING_BILLING_TYPES),
    )


def existing_recurring_periods(
    db: Session, contract_id: int | None, period_labels: Iterable[str],
) -> set[str]:
    """Dentre os period_labels informados, quais já têm mensalidade/carnê/
    pró-rata lançados para este contrato (RECURRING_BILLING_TYPES).

    A comparação é pela competência canônica: ``9/2026`` conflita com
    ``09/2026``. Rótulo sem competência (legado fora de formato) só conflita
    com o mesmo texto — é o comportamento antigo, e o relatório de saneamento
    lista esses casos.
    """
    labels = {label for label in period_labels if label}
    if not labels or not contract_id:
        return set()
    by_competencia: dict = {}
    textual: set[str] = set()
    for label in labels:
        competencia = competencia_do_rotulo(label)
        if competencia is None:
            textual.add(label)
        else:
            by_competencia.setdefault(competencia, set()).add(label)

    found: set[str] = set()
    if by_competencia:
        rows = db.scalars(
            select(Billing.competencia).where(
                Billing.contract_id == contract_id,
                Billing.competencia.in_(list(by_competencia)),
                *occupying_recurring_filter(),
            )
        ).all()
        for competencia in rows:
            found.update(by_competencia.get(competencia, ()))
    if textual:
        found.update(db.scalars(
            select(Billing.period_label).where(
                Billing.contract_id == contract_id,
                Billing.period_label.in_(textual),
                *occupying_recurring_filter(),
            )
        ).all())
    return found


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


class InstallmentSplitError(ValueError):
    """Valor não comporta a quantidade de parcelas pedida."""


def split_amount_in_installments(total: Decimal | float | str, installments: int) -> list[Decimal]:
    """Divide ``total`` em ``installments`` parcelas que somam exatamente o total,
    todas com pelo menos R$ 0,01.

    Regra histórica preservada: base arredondada (ROUND_HALF_UP) e a diferença
    na última parcela — é o que já foi emitido até hoje (100/3 → 33,33 +
    33,33 + 33,34). Quando essa regra deixaria a última parcela zerada ou
    negativa (0,02 em 4 dava 0,01×3 e -0,01; 1,00 em 60 dava -0,18), a base é
    truncada, o que mantém a última ≥ base > 0. Se nem um centavo por parcela
    cabe no total, recusa em vez de gerar parcela zero (FIN-11).
    """
    count = int(installments)
    if count < 1:
        raise InstallmentSplitError('Quantidade de parcelas deve ser pelo menos 1.')
    amount = _quantize_amount(total)
    if amount <= 0:
        raise InstallmentSplitError('Valor total deve ser maior que zero.')
    cents = int(amount * 100)
    if cents < count:
        raise InstallmentSplitError(
            f'R$ {amount:.2f} não comporta {count} parcelas de pelo menos R$ 0,01. '
            f'Use no máximo {cents} parcela(s).'
        )
    base = (amount / count).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    last = amount - base * (count - 1)
    if last <= 0:
        base = (amount / count).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        last = amount - base * (count - 1)
    return [base] * (count - 1) + [last]


def _occupied_item_installments(db: Session, item_id: int) -> set[int]:
    """Números de parcela já ocupados — mesma regra do índice
    uq_billings_item_parcela_efetiva: não removida e não cancelada, ou
    cancelada porque foi substituída (a dívida está no substituto)."""
    return set(db.scalars(
        select(Billing.installment_number).where(
            Billing.item_id == item_id,
            Billing.installment_number.is_not(None),
            Billing.is_deleted.is_(False),
            or_(
                Billing.status != BillingStatus.CANCELED,
                Billing.substituted_by_id.is_not(None),
            ),
        )
    ).all())


def generate_item_billings(
    db: Session, item: ClientChargeItem, force: bool = False, *, commit: bool = True,
) -> list[Billing]:
    """Gera as parcelas de um item de cobrança.

    ``commit=False`` para uso dentro de uma transação maior (o fechamento
    mensal): quem orquestra é que decide quando confirmar, senão um erro
    posterior deixa o fechamento gravado pela metade.

    Trava o item antes de conferir as parcelas (FIN-12). Dois fechamentos de
    meses diferentes selecionam o mesmo serviço pendente; sem a trava os dois
    viam "parcela 1 não existe" e inseriam. Agora o segundo espera o primeiro
    confirmar, relê o item (já faturado, inativo) e não gera nada. O índice
    uq_billings_item_parcela_efetiva é a barreira final se algum caminho novo
    esquecer a trava.
    """
    locked = lock_charge_items_for_update(db, [item.id])
    if not locked:
        return []
    item = locked[0]
    if not force and (item.is_deleted or not item.active):
        # Outro fechamento faturou (ou alguém removeu) enquanto este esperava.
        return []

    installments = max(int(item.installment_count or 1), 1)
    amounts = split_amount_in_installments(item.total_amount, installments)
    created: list[Billing] = []
    payer_client_id = charge_item_payer_client_id(db, item)
    occupied = _occupied_item_installments(db, item.id)
    if not force and charge_item_effective_billing_count(db, item.id) >= installments:
        # Serviço já coberto por cobrança combinada (1ª mensalidade) ou
        # negociação: parcelas por item_id não enxergam esses vínculos.
        occupied = set(range(1, installments + 1))

    for index in range(installments):
        due_date = normalize_due_date(item.start_date, index, item.start_date.day if item.start_date.day <= 28 else 28, 1)
        amount = amounts[index]
        title = item.title if installments == 1 else f'{item.title} • parcela {index + 1}/{installments}'
        # force regrava a parcela em aberto com os dados atuais do item; sem
        # force, parcela ocupada nunca é tocada.
        existing = (
            db.query(Billing)
            .filter(
                Billing.is_deleted == False,
                Billing.item_id == item.id,
                Billing.installment_number == index + 1,
                Billing.status != BillingStatus.CANCELED,
            )
            .first()
        ) if force else None
        if existing:
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
        if (index + 1) in occupied:
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
    lock: bool = True,
) -> Billing:
    """Marca uma cobrança como paga (status, data, valor, recibo).

    Reaproveitado pelo recebimento manual e pela baixa automática Ailos —
    mesma lógica do endpoint /billings/{id}/receive.
    """
    if lock:
        locked = lock_billings_for_update(db, [billing.id])
        if not locked or locked[0].is_deleted:
            raise ValueError(f'Cobrança #{billing.id} não está disponível.')
        billing = locked[0]
        if billing.status == BillingStatus.PAID:
            return billing

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
    item_ids = charge_item_ids_for_billing(db, billing)
    lock_charge_items_for_update(db, item_ids)
    for item_id in item_ids:
        refresh_charge_item_state(db, item_id, completion_date=completion_date)
    if commit:
        db.commit()
    else:
        db.flush()


_AILOS_REGISTRATION_IN_PROGRESS = ('REGISTRANDO', 'PROCESSANDO')


def cancel_open_billings_for_contract(
    db: Session,
    contract_id: int,
    reason: str,
) -> list[dict]:
    """Cancela em cascata as cobranças pendentes/vencidas de um contrato excluído.

    Sem isso, uma cobrança já gerada sobrevive à exclusão do contrato — segue
    vencendo, e até podendo ter boleto (re)emitido, sem nenhum serviço por trás.
    Retorna as cobranças cujo boleto Ailos segue ativo no banco (baixa manual).
    """
    ids = [
        row[0] for row in db.query(Billing.id).filter(
            Billing.contract_id == contract_id,
            Billing.is_deleted.is_(False),
            Billing.status.in_((BillingStatus.PENDING, BillingStatus.OVERDUE)),
        ).all()
    ]
    if not ids:
        return []
    billings = lock_billings_for_update(db, ids)
    inflight = [
        row[0] for row in db.query(AilosBoleto.billing_id).filter(
            AilosBoleto.billing_id.in_(ids),
            AilosBoleto.status_ailos.in_(_AILOS_REGISTRATION_IN_PROGRESS),
        ).all()
    ]
    if inflight:
        raise ValueError(
            f'Cobranças {inflight} aguardando registro na Ailos — tente excluir o contrato novamente em instantes.'
        )
    lock_charge_items_for_billings(db, billings)
    boletos_ativos: list[dict] = []
    for billing in billings:
        billing.status = BillingStatus.CANCELED
        marker = f'Cancelada automaticamente: {reason}'
        ab = db.query(AilosBoleto).filter_by(billing_id=billing.id).first()
        if ab and ab.linha_digitavel and ab.codigo_barras:
            boletos_ativos.append({'billing_id': billing.id, 'nosso_numero': ab.nosso_numero})
            marker += (
                f' | [ATENÇÃO] Boleto Ailos (nosso número {ab.nosso_numero or "—"}) '
                'segue ativo no banco — baixa manual pendente.'
            )
        billing.notes = f'{billing.notes} | {marker}' if billing.notes else marker
        refresh_charge_items_for_billing(db, billing, commit=False)
    return boletos_ativos


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


# ---------------------------------------------------------------------------
# Substituição e liberação de competência (FIN-01)
#
# Boleto único do fechamento e negociação (/billings/unificar) cancelam as
# originais e passam a dívida para um título novo. Até a Fase 02 o vínculo só
# existia no texto de ``notes``; excluir o substituto deixava as originais
# canceladas ocupando os meses sem nenhuma cobrança em aberto. Agora:
#
# * a original guarda ``substituted_by_id`` e continua ocupando mês/parcela;
# * cancelar/excluir o substituto exige reverter a substituição, e a reversão
#   reabre as originais (a dívida volta a ser a delas);
# * cancelamento simples continua ocupando o mês. Para cobrar o mês de novo,
#   o financeiro libera a competência — ato explícito e registrado.
# ---------------------------------------------------------------------------

class BillingSubstitutionError(ValueError):
    """Operação recusada por causa de um vínculo de substituição."""

    def __init__(self, code: str, message: str, billing_ids: list[int]):
        super().__init__(message)
        self.code = code
        self.message = message
        self.billing_ids = billing_ids

    def detail(self) -> dict:
        return {'code': self.code, 'message': self.message, 'billing_ids': self.billing_ids}


def mark_billings_substituted(originals: Iterable[Billing], substitute: Billing, marker: str) -> None:
    """Cancela as originais apontando para o título que assumiu a dívida."""
    for billing in originals:
        billing.status = BillingStatus.CANCELED
        billing.substituted_by_id = substitute.id
        billing.notes = f'{billing.notes} | {marker}' if billing.notes else marker


def substituted_originals(db: Session, substitute_id: int) -> list[Billing]:
    return list(db.scalars(
        select(Billing)
        .where(
            Billing.substituted_by_id == substitute_id,
            Billing.is_deleted.is_(False),
        )
        .order_by(Billing.id.asc())
    ).all())


def substitute_is_effective(db: Session, billing: Billing) -> bool:
    """A original está coberta por um substituto que ainda vale?"""
    if not billing.substituted_by_id:
        return False
    substitute = db.get(Billing, billing.substituted_by_id)
    return bool(
        substitute
        and not substitute.is_deleted
        and substitute.status != BillingStatus.CANCELED
    )


def restore_substituted_originals(
    db: Session,
    substitute: Billing,
    *,
    user_id: int | None,
    reason: str,
) -> list[int]:
    """Desfaz a substituição: as originais voltam a ser a dívida em aberto.

    Chamada antes de cancelar/excluir o substituto, na mesma transação. Não
    mexe no substituto — quem chama decide se ele é cancelado ou removido.
    """
    candidates = substituted_originals(db, substitute.id)
    if not candidates:
        return []
    originals = [
        billing for billing in lock_billings_for_update(db, [b.id for b in candidates])
        if not billing.is_deleted and billing.substituted_by_id == substitute.id
    ]
    registered = [
        row[0] for row in db.query(AilosBoleto.billing_id).filter(
            AilosBoleto.billing_id.in_([b.id for b in originals]),
            AilosBoleto.linha_digitavel.isnot(None),
            AilosBoleto.codigo_barras.isnot(None),
        ).order_by(AilosBoleto.billing_id.asc()).all()
    ]
    if registered:
        raise BillingSubstitutionError(
            'original_com_boleto_registrado',
            'Cobranças originais com boleto registrado na Ailos não podem ser reabertas '
            'automaticamente. Resolva a situação bancária delas antes de reverter.',
            registered,
        )
    lock_charge_items_for_billings(db, originals)
    today = hoje()
    for billing in originals:
        new_status = BillingStatus.PENDING if billing.due_date >= today else BillingStatus.OVERDUE
        db.add(BillingChangeLog(
            billing_id=billing.id,
            changed_by_user_id=user_id,
            field_name='status',
            previous_value=BillingStatus.CANCELED.value,
            new_value=new_status.value,
            justification=f'Substituição pela cobrança #{substitute.id} revertida: {reason}',
        ))
        billing.substituted_by_id = None
        billing.status = new_status
        marker = f'Reaberta: substituição pela cobrança #{substitute.id} revertida.'
        billing.notes = f'{billing.notes} | {marker}' if billing.notes else marker
    db.flush()
    for billing in originals:
        refresh_charge_items_for_billing(db, billing, commit=False)
    return [billing.id for billing in originals]


def ensure_substitution_allows_removal(
    db: Session, billing: Billing, *, revert_substitution: bool,
) -> list[Billing]:
    """Valida cancelar/excluir um título envolvido em substituição.

    Devolve as originais que precisam ser reabertas (vazio se não há). Recusa
    remover uma original cujo substituto ainda vale — isso liberaria o mês que
    a dívida do substituto está cobrindo.
    """
    if substitute_is_effective(db, billing):
        raise BillingSubstitutionError(
            'titulo_substituido',
            f'Esta cobrança foi substituída pela #{billing.substituted_by_id} e continua '
            'representando o período. Cancele ou reverta a cobrança substituta.',
            [billing.substituted_by_id],
        )
    originals = substituted_originals(db, billing.id)
    if originals and not revert_substitution:
        ids = [original.id for original in originals]
        raise BillingSubstitutionError(
            'titulo_substituto',
            f'Esta cobrança substitui {len(ids)} cobrança(s) '
            f'({", ".join(f"#{i}" for i in ids)}). Cancelar ou remover sem reverter deixaria '
            'esses períodos sem cobrança em aberto. Confirme a reversão para reabrir as originais.',
            ids,
        )
    return originals


def release_billing_competencia(
    db: Session,
    billing: Billing,
    *,
    user_id: int | None,
    justification: str,
) -> None:
    """Devolve o mês de uma mensalidade cancelada para ser cobrado de novo."""
    if billing.billing_type not in RECURRING_BILLING_TYPES:
        raise BillingSubstitutionError(
            'competencia_nao_ocupada',
            'Só mensalidade, pró-rata, 1ª cobrança e parcela de carnê ocupam a competência do contrato.',
            [billing.id],
        )
    if billing.status != BillingStatus.CANCELED:
        raise BillingSubstitutionError(
            'cobranca_nao_cancelada',
            'Só cobrança cancelada pode ter a competência liberada.',
            [billing.id],
        )
    if billing.competencia_liberada:
        return
    if substitute_is_effective(db, billing):
        raise BillingSubstitutionError(
            'titulo_substituido',
            f'Esta cobrança foi substituída pela #{billing.substituted_by_id}; o período está '
            'coberto por ela. Reverta a substituição em vez de liberar a competência.',
            [billing.substituted_by_id],
        )
    db.add(BillingChangeLog(
        billing_id=billing.id,
        changed_by_user_id=user_id,
        field_name='competencia_liberada',
        previous_value='false',
        new_value='true',
        justification=justification,
    ))
    billing.competencia_liberada = True
    marker = f'Competência {billing.period_label or "—"} liberada: {justification}'
    billing.notes = f'{billing.notes} | {marker}' if billing.notes else marker

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
