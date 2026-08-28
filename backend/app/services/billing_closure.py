from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session, aliased

from app.core.timezone import hoje
from app.models.billing import Billing
from app.models.billing_charge_item import BillingChargeItem
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.models.plan import Plan
from app.models.service_product import ServiceProduct
from app.models.tracker import Tracker
from app.models.uninstall_event import UninstallEvent
from app.models.vehicle import Vehicle
from app.services.financial import (
    _quantize_amount,
    add_months,
    associate_billing_charge_item,
    charge_item_effective_billing_count,
    contract_payer_client_id,
    decimal_to_float,
    generate_item_billings,
    normalize_due_date,
    plan_title,
    period_label_for_date,
    refresh_overdue_statuses,
)

MIN_BILLING_AMOUNT = Decimal('5.00')


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _billing_due_in_month(contract: Contract, plan: Plan, reference_month: date) -> date | None:
    interval = max(int(getattr(plan, 'billing_interval_months', 1) or 1), 1)
    billing_day = contract.billing_day or 1
    months_since_start = (
        (reference_month.year - contract.start_date.year) * 12
        + (reference_month.month - contract.start_date.month)
    )
    if months_since_start < 0:
        return None
    cycle = months_since_start // interval
    due_date = normalize_due_date(contract.start_date, cycle, billing_day, interval)
    # Never produce a due date that predates the contract start (billing_day < start day)
    if due_date < contract.start_date:
        return None
    if due_date.year == reference_month.year and due_date.month == reference_month.month:
        return due_date
    return None


def _lock_competencia(db: Session, reference_month: date) -> None:
    """Serializa fechamentos concorrentes da MESMA competência.

    Sem isto, dois fechamentos simultâneos do mesmo mês veem ``already_generated``
    False ao mesmo tempo e ambos inserem — duplicando as cobranças. O lock de
    transação (Postgres) segura o segundo até o primeiro comitar; em SQLite
    (testes, serial) é no-op.
    """
    if db.bind is None or db.bind.dialect.name != 'postgresql':
        return
    # Chave estável por competência (AAAAMM) — meses diferentes não se bloqueiam.
    chave = reference_month.year * 100 + reference_month.month
    db.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': chave})


def _has_existing_billing(db: Session, contract_id: int, period_label: str) -> bool:
    return db.query(Billing).filter(
        Billing.is_deleted.is_(False),
        Billing.contract_id == contract_id,
        Billing.period_label == period_label,
        # 'carne': parcela de carnê já cobre o mês — sem isto o fechamento
        # mensal gerava uma mensalidade recorrente POR CIMA de um mês já
        # pago via carnê (cobrança duplicada).
        Billing.billing_type.in_(['recorrente', 'prorata', 'primeira_mensalidade', 'carne']),
    ).first() is not None


def _prorata_fields(plan_price: float, start_date: date) -> tuple[bool, float, int, int]:
    """
    Retorna (is_prorata, billing_amount, prorated_days, days_in_month).
    Pro-rata se aplica apenas quando o contrato começa após o dia 1 do mês.
    """
    if start_date.day == 1:
        return False, plan_price, 0, 0
    dim = monthrange(start_date.year, start_date.month)[1]
    remaining = dim - start_date.day + 1
    prorated = float(
        (Decimal(str(plan_price)) * Decimal(remaining) / Decimal(dim)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
    )
    return True, prorated, remaining, dim


def _apply_client_scope(query, client_column, filter_type: str, client_id: int | None):
    """Aplica o mesmo recorte de clientes a qualquer categoria do fechamento."""
    eligible_clients = select(Client.id).where(Client.is_deleted.is_(False))
    if filter_type in ('pf', 'pj'):
        eligible_clients = eligible_clients.where(Client.type == filter_type)
    elif filter_type == 'client' and client_id is not None:
        eligible_clients = eligible_clients.where(Client.id == client_id)
    return query.filter(client_column.in_(eligible_clients))


def _pending_uninstall_events_for_month(
    db: Session,
    reference_month: date,
    filter_type: str = 'all',
    client_id: int | None = None,
) -> list[UninstallEvent]:
    """Eventos vencidos até a competência, inclusive os esquecidos em meses anteriores.

    ``skipped`` era o estado terminal usado para valores abaixo de R$ 5. Ele é
    incluído para recuperar dados históricos e volta a ``pending`` enquanto
    aguarda acumulação suficiente.
    """
    if reference_month.month == 12:
        month_end = date(reference_month.year + 1, 1, 1)
    else:
        month_end = date(reference_month.year, reference_month.month + 1, 1)
    query = (
        db.query(UninstallEvent)
        .filter(
            UninstallEvent.status.in_(('pending', 'skipped')),
            UninstallEvent.billing_id.is_(None),
            UninstallEvent.uninstall_date < month_end,
        )
    )
    return _apply_client_scope(
        query, UninstallEvent.client_id, filter_type, client_id,
    ).order_by(UninstallEvent.uninstall_date.asc(), UninstallEvent.id.asc()).all()


def uninstall_fee_for_event(db: Session, event: UninstallEvent) -> tuple[Decimal, str]:
    """Valor e título da taxa de um evento de desinstalação.

    ``fee_amount`` é o valor efetivamente acordado no momento da retirada e
    tem precedência absoluta: o produto de serviço define apenas O QUE foi
    cobrado (título/vínculo com o catálogo), nunca QUANTO.

    Antes as duas coisas eram somadas. Como a tela preenche a taxa direta com
    o preço do produto ao selecioná-lo, escolher um serviço de desinstalação
    cobrava o dobro de forma determinística. Somar também deixava o valor
    refém do catálogo: mudar o preço do produto depois alterava uma cobrança
    já negociada com o cliente.

    O preço do produto só é consultado como fallback, para eventos antigos
    gravados sem ``fee_amount``.
    """
    product = None
    if event.service_product_id:
        candidate = db.get(ServiceProduct, event.service_product_id)
        if candidate and not candidate.is_deleted:
            product = candidate

    if event.fee_amount is not None and Decimal(str(event.fee_amount)) > 0:
        fee_amount = Decimal(str(event.fee_amount))
    elif product is not None:
        fee_amount = Decimal(str(product.default_price))
    else:
        fee_amount = Decimal('0')

    return fee_amount, (product.name if product else 'Taxa de desinstalação')


def _due_date_for_uninstall_event(event: UninstallEvent, db: Session) -> date:
    contract = db.get(Contract, event.contract_id) if event.contract_id else None
    client = db.get(Client, event.client_id)
    billing_day = (
        (contract.billing_day if contract and contract.billing_day else None)
        or (client.billing_day if client and client.billing_day else None)
        or 1
    )
    dim = monthrange(event.uninstall_date.year, event.uninstall_date.month)[1]
    fee_billing_day = min(billing_day, dim)
    due = date(event.uninstall_date.year, event.uninstall_date.month, fee_billing_day)
    if due <= event.uninstall_date:
        due = add_months(due, 1)
    return due


def _uninstall_event_payer_client_id(db: Session, event: UninstallEvent) -> int:
    """Pagador do evento, sem aceitar referência contratual inconsistente."""
    if event.payer_client_id:
        payer = db.get(Client, event.payer_client_id)
        if not payer or payer.is_deleted:
            raise ValueError(
                f'Responsável financeiro #{event.payer_client_id} do evento '
                f'#{event.id} não está disponível.'
            )
        return payer.id
    if not event.contract_id:
        return event.client_id
    contract = db.get(Contract, event.contract_id)
    if not contract or contract.is_deleted:
        raise ValueError(f'Contrato #{event.contract_id} da desinstalação não está disponível.')
    if contract.client_id != event.client_id:
        raise ValueError(
            f'Evento #{event.id} e contrato #{contract.id} pertencem a clientes diferentes.'
        )
    return contract_payer_client_id(db, contract)


def _first_cycle_charge_items(
    db: Session,
    client_id: int,
    contract_id: int,
    reference_month: date,
) -> list[dict]:
    """
    Retorna ChargeItems de parcela única (installment_count=1) que:
    - Pertencem explicitamente ao contrato (itens genéricos ficam avulsos)
    - Têm start_date dentro do mês de referência (serviços do início do contrato)
    - Ainda não foram faturados (active=True e billing_count=0)

    Esses itens serão embutidos na cobrança combinada da primeira mensalidade,
    em vez de gerar faturas separadas.
    """
    month_start = reference_month.replace(day=1)
    if reference_month.month == 12:
        month_end = date(reference_month.year + 1, 1, 1)
    else:
        month_end = date(reference_month.year, reference_month.month + 1, 1)

    q = db.query(ClientChargeItem).filter(
        ClientChargeItem.is_deleted.is_(False),
        ClientChargeItem.active.is_(True),
        ClientChargeItem.client_id == client_id,
        ClientChargeItem.contract_id == contract_id,
        ClientChargeItem.installment_count == 1,
        ClientChargeItem.start_date >= month_start,
        ClientChargeItem.start_date < month_end,
    )
    result = []
    for item in q.all():
        billing_count = charge_item_effective_billing_count(db, item.id)
        if billing_count > 0:
            continue  # já faturado por outro caminho

        result.append({
            'item_id': item.id,
            'title': item.title,
            'amount': float(_quantize_amount(item.total_amount)),
        })

    return result


def _effective_billing_counts_bulk(db: Session, item_ids: list[int]) -> dict[int, int]:
    """Mesma regra de `charge_item_effective_billing_count` (financial.py), mas
    para vários itens de uma vez — evita 1 query por item nos loops de
    fechamento (`_pending_charge_items`), que rodam sobre todos os serviços
    avulsos pendentes do mês.
    """
    if not item_ids:
        return {}
    billing_ids_by_item: dict[int, set[int]] = defaultdict(set)
    direct_rows = db.query(Billing.item_id, Billing.id).filter(
        Billing.is_deleted.is_(False),
        Billing.status != BillingStatus.CANCELED,
        Billing.item_id.in_(item_ids),
    ).all()
    for item_id, billing_id in direct_rows:
        billing_ids_by_item[item_id].add(billing_id)
    assoc_rows = (
        db.query(BillingChargeItem.item_id, BillingChargeItem.billing_id)
        .join(Billing, Billing.id == BillingChargeItem.billing_id)
        .filter(
            BillingChargeItem.item_id.in_(item_ids),
            Billing.is_deleted.is_(False),
            Billing.status != BillingStatus.CANCELED,
        )
        .all()
    )
    for item_id, billing_id in assoc_rows:
        billing_ids_by_item[item_id].add(billing_id)
    return {item_id: len(ids) for item_id, ids in billing_ids_by_item.items()}


def _pending_charge_items(
    db: Session,
    reference_month: date,
    exclude_ids: set[int] | None = None,
    filter_type: str = 'all',
    client_id: int | None = None,
) -> list[dict]:
    """
    Retorna ClientChargeItems ativos cujos billings ainda não foram totalmente gerados
    e cujo start_date é anterior ao final do mês de referência.
    exclude_ids: item_ids já embutidos em cobranças combinadas de primeiro mês.
    """
    if reference_month.month == 12:
        month_end = date(reference_month.year + 1, 1, 1)
    else:
        month_end = date(reference_month.year, reference_month.month + 1, 1)

    query = (
        db.query(ClientChargeItem)
        .filter(
            ClientChargeItem.is_deleted.is_(False),
            ClientChargeItem.active.is_(True),
            ClientChargeItem.start_date < month_end,
        )
    )
    items = _apply_client_scope(
        query, ClientChargeItem.client_id, filter_type, client_id,
    ).order_by(ClientChargeItem.id.asc()).all()

    candidate_items = [
        item for item in items if not (exclude_ids and item.id in exclude_ids)
    ]
    billing_counts = _effective_billing_counts_bulk(db, [item.id for item in candidate_items])

    client_ids = {item.client_id for item in candidate_items}
    contract_ids = {item.contract_id for item in candidate_items if item.contract_id}
    vehicle_ids = {item.vehicle_id for item in candidate_items if item.vehicle_id}
    client_map = {
        c.id: c for c in db.scalars(select(Client).where(Client.id.in_(client_ids))).all()
    } if client_ids else {}
    contract_map = {
        c.id: c for c in db.scalars(select(Contract).where(Contract.id.in_(contract_ids))).all()
    } if contract_ids else {}
    vehicle_map = {
        v.id: v for v in db.scalars(select(Vehicle).where(Vehicle.id.in_(vehicle_ids))).all()
    } if vehicle_ids else {}

    result = []
    for item in candidate_items:
        billing_count = billing_counts.get(item.id, 0)

        installments = max(int(item.installment_count or 1), 1)
        if billing_count >= installments:
            continue

        if item.contract_id:
            item_contract = contract_map.get(item.contract_id)
            if not item_contract or item_contract.is_deleted:
                raise ValueError(
                    f'Lançamento #{item.id} está ativo, mas referencia contrato '
                    f'removido #{item.contract_id}. Reconcilie o lançamento antes do fechamento.'
                )

        client = client_map.get(item.client_id)
        vehicle = vehicle_map.get(item.vehicle_id) if item.vehicle_id else None
        remaining = installments - billing_count
        total = Decimal(str(item.total_amount))
        per_installment = (total / installments).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        result.append({
            'type': 'servico',
            'item_id': item.id,
            'client_id': item.client_id,
            'client_name': client.name if client else f'Cliente #{item.client_id}',
            'client_type': client.type if client else 'pf',
            'vehicle_plate': vehicle.plate if vehicle else None,
            'title': item.title,
            'installment_count': installments,
            'generated_count': billing_count,
            'remaining_installments': remaining,
            'per_installment_amount': float(per_installment),
            'total_remaining': float(per_installment * remaining),
            'start_date': item.start_date,
        })

    return result


def _validate_contract_relationships(
    contract: Contract,
    client: Client | None,
    plan: Plan | None,
    vehicle: Vehicle | None,
    tracker: Tracker | None,
    interveniente: Client | None,
) -> None:
    """Falha fechada para contrato operacional com referência removida.

    A validação é local ao fechamento. Referências históricas de eventos de
    desinstalação não passam por ela e continuam podendo apontar para contrato
    ou veículo removido.
    """
    if not client or client.is_deleted:
        raise ValueError(
            f'Contrato #{contract.id} referencia cliente removido #{contract.client_id}.'
        )
    if not plan or plan.is_deleted:
        raise ValueError(
            f'Contrato #{contract.id} referencia plano removido #{contract.plan_id}.'
        )
    if contract.vehicle_id and (not vehicle or vehicle.is_deleted):
        raise ValueError(
            f'Contrato #{contract.id} referencia veículo removido #{contract.vehicle_id}.'
        )
    if contract.tracker_id and (not tracker or tracker.is_deleted):
        raise ValueError(
            f'Contrato #{contract.id} referencia rastreador removido #{contract.tracker_id}.'
        )
    if contract.interveniente_client_id and (
        not interveniente or interveniente.is_deleted
    ):
        raise ValueError(
            f'Responsável financeiro #{contract.interveniente_client_id} do contrato '
            f'#{contract.id} não está disponível.'
        )


def _locked_contracts(db: Session, contract_ids: set[int]) -> dict[int, Contract]:
    """Trava, em ordem estável, somente contratos usados nesta geração."""
    if not contract_ids:
        return {}
    rows = db.scalars(
        select(Contract)
        .where(Contract.id.in_(contract_ids))
        .order_by(Contract.id.asc())
        .with_for_update()
        # O contrato pode ter sido carregado pela simulação antes de a
        # exclusão concorrente comitar. Atualize o objeto do identity map com a
        # versão que o SELECT FOR UPDATE acabou de serializar.
        .execution_options(populate_existing=True)
    ).all()
    return {contract.id: contract for contract in rows}


def _validate_locked_contract_for_closure(db: Session, contract: Contract) -> None:
    client = db.get(Client, contract.client_id)
    plan = db.get(Plan, contract.plan_id)
    vehicle = db.get(Vehicle, contract.vehicle_id) if contract.vehicle_id else None
    tracker = db.get(Tracker, contract.tracker_id) if contract.tracker_id else None
    interveniente = (
        db.get(Client, contract.interveniente_client_id)
        if contract.interveniente_client_id else None
    )
    _validate_contract_relationships(
        contract, client, plan, vehicle, tracker, interveniente,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def simulate_closure(
    db: Session,
    reference_month: date,
    filter_type: str = 'all',
    client_id: int | None = None,
    *,
    commit: bool = True,
) -> dict:
    # commit=False quando chamada de dentro de execute_closure: o refresh abaixo
    # comitava e, com isso, encerrava a transação que segura o lock da
    # competência — na prática o lock morria aqui, antes de qualquer cobrança
    # ser criada.
    refresh_overdue_statuses(db, commit=commit)

    # Cliente que responde pela cobrança (interveniente). Sem ele, o próprio
    # cliente do contrato é o responsável — é por ele que o relatório agrupa.
    Interveniente = aliased(Client)

    query = db.query(Contract, Client, Plan, Vehicle, Tracker, Interveniente).join(
        Client, Client.id == Contract.client_id
    ).join(
        Plan, Plan.id == Contract.plan_id
    ).outerjoin(
        Vehicle, Vehicle.id == Contract.vehicle_id
    ).outerjoin(
        Tracker, Tracker.id == Contract.tracker_id
    ).outerjoin(
        Interveniente, Interveniente.id == Contract.interveniente_client_id
    ).filter(
        Contract.is_deleted.is_(False),
        Contract.status == 'ativo',
        # Contrato com vigência já encerrada antes do mês de referência não entra
        # no fechamento — senão sairia boleto/NFS-e de um contrato que acabou.
        or_(Contract.end_date.is_(None), Contract.end_date >= reference_month),
    )

    if filter_type == 'pf':
        query = query.filter(Client.type == 'pf')
    elif filter_type == 'pj':
        query = query.filter(Client.type == 'pj')
    elif filter_type == 'client' and client_id:
        query = query.filter(Client.id == client_id)

    items = []
    for contract, client, plan, vehicle, tracker, interveniente in query.all():
        _validate_contract_relationships(
            contract, client, plan, vehicle, tracker, interveniente,
        )
        due_date = _billing_due_in_month(contract, plan, reference_month)
        if due_date is None:
            continue

        interval = max(int(getattr(plan, 'billing_interval_months', 1) or 1), 1)
        period_label = period_label_for_date(due_date, interval)
        already = _has_existing_billing(db, contract.id, period_label)
        plan_price = decimal_to_float(plan.price)

        # First billing month: normally the start month, but shifts to the NEXT month
        # when billing_day < start_date.day (that calendar day has already passed).
        _billing_day = contract.billing_day or 1
        if _billing_day >= contract.start_date.day:
            _first_billing_month = contract.start_date.replace(day=1)
        else:
            _first_billing_month = add_months(contract.start_date.replace(day=1), 1)

        first_cycle = (
            reference_month.year == _first_billing_month.year
            and reference_month.month == _first_billing_month.month
        )
        if first_cycle:
            is_prorata, billing_amount, prorated_days, days_in_month = _prorata_fields(
                plan_price, contract.start_date
            )
            # Charge items with start_date in the contract's start month are embedded
            first_charges = _first_cycle_charge_items(
                db, client.id, contract.id,
                contract.start_date.replace(day=1),
            )
        else:
            is_prorata, billing_amount, prorated_days, days_in_month = False, plan_price, 0, 0
            first_charges = []

        total_first_billing = billing_amount + sum(c['amount'] for c in first_charges)

        items.append({
            'type': 'recorrente',
            'contract_id': contract.id,
            'client_id': client.id,
            'client_name': client.name,
            'payer_client_id': contract_payer_client_id(db, contract),
            'payer_name': (
                interveniente.name if interveniente and not interveniente.is_deleted else client.name
            ),
            'client_type': client.type,
            'vehicle_plate': vehicle.plate if vehicle else None,
            'tracker_imei': tracker.imei if tracker else None,
            'plan_name': plan.name,
            'plan_price': plan_price,
            'billing_amount': billing_amount,
            'is_prorata': is_prorata,
            'prorated_days': prorated_days,
            'days_in_month': days_in_month,
            'first_month_charges': first_charges,
            'total_first_billing': total_first_billing,
            'period_label': period_label,
            'due_date': due_date,
            'already_generated': already,
            'billing_day': contract.billing_day,
            # Campos usados pelo relatório de simulação (formato do SGR):
            # agrupa por interveniente e detalha veículo + rastreadores.
            'interveniente_nome': (interveniente.name if interveniente else client.name),
            'vehicle_id': vehicle.id if vehicle else None,
            'vehicle_type': (vehicle.type if vehicle else None),
            'vehicle_created_at': (
                vehicle.created_at.date() if vehicle and getattr(vehicle, 'created_at', None) else None
            ),
            'contract_start_date': contract.start_date,
            'tracker_install_date': (tracker.install_date if tracker else None),
        })

    # Exclui dos serviços avulsos os itens já embutidos em cobranças de primeiro mês
    embedded_ids: set[int] = {
        c['item_id']
        for item in items
        for c in item['first_month_charges']
    }

    # Eventos de desinstalação vencidos até esta competência. Valores pequenos
    # são acumulados por cliente; nunca mais viram perda terminal em ``skipped``.
    uninstall_events = _pending_uninstall_events_for_month(
        db, reference_month, filter_type, client_id,
    )
    uninstall_amounts = {
        event.id: uninstall_fee_for_event(db, event)[0]
        for event in uninstall_events
    }
    payer_ids_by_event = {
        event.id: _uninstall_event_payer_client_id(db, event)
        for event in uninstall_events
    }
    totals_by_payer: dict[int, Decimal] = defaultdict(lambda: Decimal('0.00'))
    for event in uninstall_events:
        totals_by_payer[payer_ids_by_event[event.id]] += uninstall_amounts[event.id]

    uninstall_items = []
    for event in uninstall_events:
        client = db.get(Client, event.client_id)
        vehicle = db.get(Vehicle, event.vehicle_id)
        payer_id = payer_ids_by_event[event.id]
        payer = db.get(Client, payer_id)
        fee_amount = uninstall_amounts[event.id]
        aggregation_total = totals_by_payer[payer_id]
        deferred = aggregation_total < MIN_BILLING_AMOUNT
        uninstall_items.append({
            'type': 'taxa_desinstalacao',
            'event_id': event.id,
            'client_id': event.client_id,
            'client_name': client.name if client else f'Cliente #{event.client_id}',
            'payer_client_id': payer_id,
            'payer_name': payer.name if payer else f'Responsável financeiro #{payer_id}',
            'client_type': client.type if client else 'pf',
            'vehicle_plate': vehicle.plate if vehicle else None,
            'uninstall_date': event.uninstall_date,
            'fee_amount': float(fee_amount),
            'deferred': deferred,
            # Compatibilidade temporária para clientes antigos da API. Agora
            # significa "não faturado nesta rodada", sem mudar o evento para
            # um estado terminal.
            'skipped': deferred,
            'skip_reason': (
                f'Acumulado do cliente em R$ {float(aggregation_total):.2f}; '
                f'aguardando mínimo de R$ {float(MIN_BILLING_AMOUNT):.2f}'
                if deferred else None
            ),
            'aggregation_total': float(aggregation_total),
        })

    # Serviços / cobranças avulsas pendentes (exclui os embutidos)
    charge_items = _pending_charge_items(
        db, reference_month, exclude_ids=embedded_ids,
        filter_type=filter_type, client_id=client_id,
    )

    to_generate = [i for i in items if not i['already_generated']]
    already_done = [i for i in items if i['already_generated']]
    # total_amount inclui os serviços embutidos na primeira cobrança
    total_amount = sum(i['total_first_billing'] for i in to_generate)
    total_uninstall = sum(i['fee_amount'] for i in uninstall_items if not i['deferred'])
    total_services = sum(i['total_remaining'] for i in charge_items)

    return {
        'reference_month': reference_month.strftime('%m/%Y'),
        'total_contracts': len(items),
        'to_generate': len(to_generate),
        'already_generated': len(already_done),
        'total_amount': round(total_amount, 2),
        'items': items,
        'uninstall_events': uninstall_items,
        'total_uninstall_fees': round(total_uninstall, 2),
        'charge_items': charge_items,
        'total_services': round(total_services, 2),
        'grand_total': round(total_amount + total_uninstall + total_services, 2),
    }


def execute_closure(
    db: Session,
    reference_month: date,
    filter_type: str = 'all',
    client_id: int | None = None,
    contract_ids: list[int] | None = None,
    uninstall_event_ids: list[int] | None = None,
    charge_item_ids: list[int] | None = None,
) -> dict:
    # Trava a competência ANTES de simular: simulação + geração ficam atômicas em
    # relação a outro fechamento do mesmo mês, fechando a corrida de duplicação.
    # O lock é de TRANSAÇÃO (pg_advisory_xact_lock), então nada daqui até o
    # commit final pode comitar — daí o commit=False propagado abaixo.
    _lock_competencia(db, reference_month)
    simulation = simulate_closure(db, reference_month, filter_type, client_id, commit=False)
    # Ao receber qualquer lista de seleção, opera em modo snapshot/fail-closed:
    # categorias omitidas significam seleção vazia, não "processar tudo". Sem
    # lista alguma preservamos o comando administrativo de fechamento integral.
    exact_selection = any(
        ids is not None
        for ids in (contract_ids, uninstall_event_ids, charge_item_ids)
    )
    to_generate = [i for i in simulation['items'] if not i['already_generated']]
    if exact_selection:
        selected_contracts = set(contract_ids or [])
        to_generate = [
            item for item in to_generate
            if item['contract_id'] in selected_contracts
        ]

    deferred_item_ids = {
        item['event_id'] for item in simulation['uninstall_events'] if item['deferred']
    }
    selected_uninstall_items = [
        item for item in simulation['uninstall_events'] if not item['deferred']
    ]
    if exact_selection:
        selected = set(uninstall_event_ids or [])
        selected_uninstall_items = [
            item for item in selected_uninstall_items if item['event_id'] in selected
        ]

    selected_charge_items = list(simulation['charge_items'])
    if exact_selection:
        selected = set(charge_item_ids or [])
        selected_charge_items = [
            item for item in selected_charge_items if item['item_id'] in selected
        ]

    recurring_contract_ids = {item['contract_id'] for item in to_generate}
    selected_charge_ids = {item['item_id'] for item in selected_charge_items}
    charge_contract_ids = set(db.scalars(
        select(ClientChargeItem.contract_id).where(
            ClientChargeItem.id.in_(selected_charge_ids),
            ClientChargeItem.contract_id.is_not(None),
        )
    ).all()) if selected_charge_ids else set()
    relevant_contract_ids = recurring_contract_ids | charge_contract_ids
    locked_contracts = _locked_contracts(db, relevant_contract_ids)

    for contract_id in sorted(relevant_contract_ids):
        locked_contract = locked_contracts.get(contract_id)
        if not locked_contract or locked_contract.is_deleted:
            raise ValueError(
                f'Contrato #{contract_id} foi removido durante o fechamento. '
                'Refaça a simulação.'
            )
        if contract_id in recurring_contract_ids and locked_contract.status != 'ativo':
            raise ValueError(
                f'Contrato #{contract_id} deixou de estar ativo durante o fechamento. '
                'Refaça a simulação.'
            )
        if locked_contract.status == 'ativo':
            _validate_locked_contract_for_closure(db, locked_contract)

    # Somente depois dos locks e da revalidação começam as mutações. Assim,
    # um contrato removido durante a simulação falha antes de criar cobranças.
    for event_id in deferred_item_ids:
        deferred_event = db.get(UninstallEvent, event_id)
        if deferred_event and deferred_event.status == 'skipped':
            deferred_event.status = 'pending'
            deferred_event.processed_at = None

    created_ids = []
    for item in to_generate:
        contract = locked_contracts[item['contract_id']]
        plan = db.get(Plan, contract.plan_id)
        if not plan or plan.is_deleted:  # defesa adicional após a revalidação
            raise ValueError(
                f'Contrato #{contract.id} referencia plano removido #{contract.plan_id}.'
            )

        billing_amount = _quantize_amount(item['billing_amount'])
        first_charges = item.get('first_month_charges', [])

        if first_charges:
            # Cobrança combinada: mensalidade + serviços do primeiro mês
            service_total = sum(Decimal(str(c['amount'])) for c in first_charges)
            combined_amount = billing_amount + service_total

            service_parts = ' | '.join(
                f'{c["title"]}: R$ {c["amount"]:.2f}' for c in first_charges
            )
            if item['is_prorata']:
                plan_label = f'{plan_title(plan)} pró-rata {item["prorated_days"]} dias'
            else:
                plan_label = plan_title(plan)

            title = f'1ª cobrança — {plan_label}'
            notes = (
                f'Mensalidade ({plan.name}): R$ {float(billing_amount):.2f} | '
                + service_parts
                + f' | Total: R$ {float(combined_amount):.2f}'
            )
            billing_type = 'primeira_mensalidade'

            billing = Billing(
                contract_id=contract.id,
                client_id=contract.client_id,
                payer_client_id=contract_payer_client_id(db, contract),
                vehicle_id=getattr(contract, 'vehicle_id', None),
                tracker_id=getattr(contract, 'tracker_id', None),
                amount=combined_amount,
                due_date=item['due_date'],
                status=BillingStatus.PENDING if item['due_date'] >= hoje() else BillingStatus.OVERDUE,
                period_label=item['period_label'],
                payment_method=contract.payment_method,
                notes=notes,
                title=title,
                billing_type=billing_type,
            )
            db.add(billing)
            db.flush()
            created_ids.append(billing.id)

            # Registra cada serviço no título combinado. A emissão apenas o marca
            # como faturado; a conclusão é derivada da baixa do pagamento.
            for charge in first_charges:
                charge_obj = db.get(ClientChargeItem, charge['item_id'])
                if charge_obj:
                    associate_billing_charge_item(db, billing, charge_obj, charge['amount'])
                    charge_obj.active = False
                    charge_obj.completed_at = None
                    charge_obj.status = 'faturado'

        else:
            # Cobrança normal (mensalidade ou pró-rata sem serviços embutidos)
            if item['is_prorata']:
                title = f'{plan_title(plan)} — pró-rata {item["prorated_days"]} dias'
                notes = (
                    f'Pró-rata: {item["prorated_days"]} de {item["days_in_month"]} dias'
                    f' — {item["period_label"]}'
                )
                billing_type = 'prorata'
            else:
                title = plan_title(plan)
                notes = f'Fechamento — {item["period_label"]}'
                billing_type = 'recorrente'

            billing = Billing(
                contract_id=contract.id,
                client_id=contract.client_id,
                payer_client_id=contract_payer_client_id(db, contract),
                vehicle_id=getattr(contract, 'vehicle_id', None),
                tracker_id=getattr(contract, 'tracker_id', None),
                amount=billing_amount,
                due_date=item['due_date'],
                status=BillingStatus.PENDING if item['due_date'] >= hoje() else BillingStatus.OVERDUE,
                period_label=item['period_label'],
                payment_method=contract.payment_method,
                notes=notes,
                title=title,
                billing_type=billing_type,
            )
            db.add(billing)
            db.flush()
            created_ids.append(billing.id)

    # ── Boleto único por cliente (boleto_format='unico' no cadastro) ────────
    # Junta as MENSALIDADES normais recém-criadas do mesmo cliente em UMA
    # cobrança só (1 boleto = 1 tarifa bancária/mês, como o campo do cadastro
    # promete). Pró-rata e 1ª cobrança ficam de fora (semântica própria).
    # As individuais são canceladas com referência cruzada — e continuam
    # contando para a idempotência (_has_existing_billing ignora o status).
    consolidated_ids: list[int] = []
    _por_pagador: dict[int, list[Billing]] = defaultdict(list)
    for bid in created_ids:
        b = db.get(Billing, bid)
        if b and b.billing_type == 'recorrente':
            _por_pagador[b.payer_client_id or b.client_id].append(b)

    for payer_id, grupo in _por_pagador.items():
        if len(grupo) < 2:
            continue
        cliente = db.get(Client, payer_id)
        # Só consolida com a opção EXPLÍCITA no cadastro (campo vazio = individual)
        if not cliente or cliente.boleto_format != 'unico':
            continue

        total = sum(Decimal(str(b.amount)) for b in grupo)
        venc = max(b.due_date for b in grupo)
        period = grupo[0].period_label

        def _placa(b: Billing) -> str:
            v = db.get(Vehicle, b.vehicle_id) if b.vehicle_id else None
            return v.plate if v and not v.is_deleted else (b.title or f'#{b.id}')

        detalhes = ' | '.join(f'{_placa(b)}: R$ {float(b.amount):.2f}' for b in grupo)
        owner_ids = {billing.client_id for billing in grupo}
        unico = Billing(
            # Se há vários clientes atendidos pelo mesmo interveniente, o título
            # consolidado não pertence exclusivamente a nenhum deles.
            client_id=(next(iter(owner_ids)) if len(owner_ids) == 1 else payer_id),
            payer_client_id=payer_id,
            billing_type='recorrente',
            title=f'Mensalidades — {len(grupo)} veículos (boleto único)',
            amount=total,
            due_date=venc,
            status=BillingStatus.PENDING if venc >= hoje() else BillingStatus.OVERDUE,
            period_label=period,
            payment_method=grupo[0].payment_method,
            notes=f'Boleto único ({period}): {detalhes}',
        )
        db.add(unico)
        db.flush()
        for b in grupo:
            b.status = BillingStatus.CANCELED
            marker = f'Consolidada no boleto único #{unico.id}.'
            b.notes = f'{b.notes} | {marker}' if b.notes else marker
            created_ids.remove(b.id)
        created_ids.append(unico.id)
        consolidated_ids.append(unico.id)

    # Processa SOMENTE os eventos que pertenciam à simulação e foram enviados
    # pelo cliente da API. Isso fecha o TOCTOU em que uma taxa criada depois da
    # prévia entrava silenciosamente no fechamento.
    uninstall_billing_ids: list[int] = []
    deferred_events = len(deferred_item_ids)
    processed_events = 0
    allowed_event_ids = {item['event_id'] for item in selected_uninstall_items}
    uninstall_events = [
        event for event in _pending_uninstall_events_for_month(
            db, reference_month, filter_type, client_id,
        )
        if event.id in allowed_event_ids
    ]
    now_utc = datetime.now(timezone.utc)

    events_by_payer: dict[int, list[UninstallEvent]] = defaultdict(list)
    for event in uninstall_events:
        events_by_payer[_uninstall_event_payer_client_id(db, event)].append(event)

    for event_payer_id, events in events_by_payer.items():
        event_amounts = {
            event.id: uninstall_fee_for_event(db, event)[0]
            for event in events
        }
        total_fee = sum(event_amounts.values(), Decimal('0.00'))
        if total_fee < MIN_BILLING_AMOUNT:
            # Recupera inclusive os antigos ``skipped``. O grupo permanece
            # pendente e poderá ser somado a eventos de competências futuras.
            for event in events:
                event.status = 'pending'
                event.processed_at = None
            deferred_events += len(events)
            continue

        due_date = max(_due_date_for_uninstall_event(event, db) for event in events)
        # Só associa a cobrança agregada a um contrato quando TODOS os eventos
        # pertencem explicitamente ao mesmo contrato. Misturar evento sem
        # contrato com evento contratado e escolher o único ID conhecido
        # produziria uma associação contábil enganosa.
        contract_ids_in_group = {event.contract_id for event in events}
        owner_ids_in_group = {event.client_id for event in events}
        single_event = events[0] if len(events) == 1 else None
        if single_event is not None:
            _, fee_title = uninstall_fee_for_event(db, single_event)
        else:
            fee_title = f'Taxas de desinstalação agrupadas ({len(events)} eventos)'

        detail_parts = []
        for event in events:
            vehicle = db.get(Vehicle, event.vehicle_id)
            detail_parts.append(
                f'#{event.id} {vehicle.plate if vehicle else "veículo removido"} '
                f'{event.uninstall_date.strftime("%d/%m/%Y")}: '
                f'R$ {float(event_amounts[event.id]):.2f}'
            )
        fee_billing = Billing(
            contract_id=(
                next(iter(contract_ids_in_group))
                if len(contract_ids_in_group) == 1 and None not in contract_ids_in_group
                else None
            ),
            client_id=(
                next(iter(owner_ids_in_group))
                if len(owner_ids_in_group) == 1 else event_payer_id
            ),
            payer_client_id=event_payer_id,
            vehicle_id=(single_event.vehicle_id if single_event else None),
            tracker_id=(single_event.tracker_id if single_event else None),
            title=fee_title,
            billing_type='taxa_desinstalacao',
            amount=total_fee,
            due_date=due_date,
            status=BillingStatus.PENDING if due_date >= hoje() else BillingStatus.OVERDUE,
            period_label=due_date.strftime('%m/%Y'),
            notes='Taxas processadas no fechamento: ' + ' | '.join(detail_parts),
        )
        db.add(fee_billing)
        db.flush()
        for event in events:
            event.status = 'processed'
            event.billing_id = fee_billing.id
            event.processed_at = now_utc
            processed_events += 1
        uninstall_billing_ids.append(fee_billing.id)

    # Gera billings para serviços/cobranças avulsas pendentes (os não embutidos)
    services_generated = 0
    service_billing_ids: list[int] = []
    for charge_item_dict in selected_charge_items:
        item_obj = db.get(ClientChargeItem, charge_item_dict['item_id'])
        if item_obj:
            new_billings = generate_item_billings(db, item_obj, commit=False)
            for b in new_billings:
                service_billing_ids.append(b.id)
            services_generated += len(new_billings)

    # Único commit do fechamento: até aqui nada foi confirmado, então uma falha
    # em qualquer etapa acima desfaz o fechamento inteiro em vez de deixar
    # metade das cobranças gravadas.
    db.commit()
    refresh_overdue_statuses(db)

    # Mensalidades: soma o valor real criado (combined ou normal)
    total_mensalidades = round(
        sum(float(db.get(Billing, bid).amount) for bid in created_ids), 2
    )
    total_uninstall_amount = round(
        sum(float(db.get(Billing, bid).amount) for bid in uninstall_billing_ids), 2
    )
    total_services_amount = round(
        sum(float(db.get(Billing, bid).amount) for bid in service_billing_ids), 2
    )

    return {
        'reference_month': simulation['reference_month'],
        'generated': len(created_ids),
        'billing_ids': created_ids,
        'consolidated_unico': len(consolidated_ids),
        'total_amount': total_mensalidades,
        'uninstall_fees_generated': len(uninstall_billing_ids),
        'uninstall_events_processed': processed_events,
        'uninstall_fees_deferred': deferred_events,
        # Compatibilidade com consumidores antigos: eventos não são mais
        # descartados, portanto o total de ignorados é sempre zero.
        'uninstall_fees_skipped': 0,
        'uninstall_billing_ids': uninstall_billing_ids,
        'services_generated': services_generated,
        'service_billing_ids': service_billing_ids,
        'total_services_amount': total_services_amount,
        'grand_total': round(total_mensalidades + total_uninstall_amount + total_services_amount, 2),
    }



# ---------------------------------------------------------------------------
# Relatório de simulação de fechamento — movido para billing_closure_report.py
# (BE-03): não toca em banco, só transforma o dict de simulate_closure em
# texto/XLSX/PDF. Reexportado aqui porque api/v1/endpoints/billing_closure.py
# e os testes de serviço importam esses nomes de app.services.billing_closure.
# ---------------------------------------------------------------------------
from app.services.billing_closure_report import (  # noqa: F401
    generate_closure_pdf,
    generate_closure_xlsx,
    montar_linhas_simulacao,
)
