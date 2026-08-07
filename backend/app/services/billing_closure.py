from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, aliased

from app.models.billing import Billing
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


def _has_existing_billing(db: Session, contract_id: int, period_label: str) -> bool:
    return db.query(Billing).filter(
        Billing.is_deleted.is_(False),
        Billing.contract_id == contract_id,
        Billing.period_label == period_label,
        Billing.billing_type.in_(['recorrente', 'prorata', 'primeira_mensalidade']),
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


def _pending_uninstall_events_for_month(db: Session, reference_month: date) -> list[UninstallEvent]:
    month_start = reference_month.replace(day=1)
    if reference_month.month == 12:
        month_end = date(reference_month.year + 1, 1, 1)
    else:
        month_end = date(reference_month.year, reference_month.month + 1, 1)
    return (
        db.query(UninstallEvent)
        .filter(
            UninstallEvent.status == 'pending',
            UninstallEvent.uninstall_date >= month_start,
            UninstallEvent.uninstall_date < month_end,
        )
        .all()
    )


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


def _first_cycle_charge_items(
    db: Session,
    client_id: int,
    vehicle_id: int | None,
    reference_month: date,
) -> list[dict]:
    """
    Retorna ChargeItems de parcela única (installment_count=1) que:
    - Pertencem ao cliente/veículo do contrato
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
        ClientChargeItem.installment_count == 1,
        ClientChargeItem.start_date >= month_start,
        ClientChargeItem.start_date < month_end,
    )
    if vehicle_id:
        q = q.filter(
            or_(
                ClientChargeItem.vehicle_id == vehicle_id,
                ClientChargeItem.vehicle_id.is_(None),
            )
        )

    result = []
    for item in q.all():
        billing_count = db.query(func.count(Billing.id)).filter(
            Billing.is_deleted.is_(False),
            Billing.item_id == item.id,
        ).scalar() or 0
        if billing_count > 0:
            continue  # já faturado por outro caminho

        result.append({
            'item_id': item.id,
            'title': item.title,
            'amount': float(_quantize_amount(item.total_amount)),
        })

    return result


def _pending_charge_items(
    db: Session,
    reference_month: date,
    exclude_ids: set[int] | None = None,
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

    items = (
        db.query(ClientChargeItem)
        .filter(
            ClientChargeItem.is_deleted.is_(False),
            ClientChargeItem.active.is_(True),
            ClientChargeItem.start_date < month_end,
        )
        .all()
    )

    result = []
    for item in items:
        if exclude_ids and item.id in exclude_ids:
            continue

        billing_count = db.query(func.count(Billing.id)).filter(
            Billing.is_deleted.is_(False),
            Billing.item_id == item.id,
        ).scalar() or 0

        installments = max(int(item.installment_count or 1), 1)
        if billing_count >= installments:
            continue

        client = db.get(Client, item.client_id)
        vehicle = db.get(Vehicle, item.vehicle_id) if item.vehicle_id else None
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def simulate_closure(
    db: Session,
    reference_month: date,
    filter_type: str = 'all',
    client_id: int | None = None,
) -> dict:
    refresh_overdue_statuses(db)

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
        Client.is_deleted.is_(False),
        Plan.is_deleted.is_(False),
    )

    if filter_type == 'pf':
        query = query.filter(Client.type == 'pf')
    elif filter_type == 'pj':
        query = query.filter(Client.type == 'pj')
    elif filter_type == 'client' and client_id:
        query = query.filter(Client.id == client_id)

    items = []
    for contract, client, plan, vehicle, tracker, interveniente in query.all():
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
                db, client.id, vehicle.id if vehicle else None,
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

    # Eventos de desinstalação pendentes no mês
    uninstall_events = _pending_uninstall_events_for_month(db, reference_month)
    uninstall_items = []
    for event in uninstall_events:
        client = db.get(Client, event.client_id)
        vehicle = db.get(Vehicle, event.vehicle_id)
        fee_amount = Decimal(str(event.fee_amount)) if event.fee_amount else Decimal('0')
        if event.service_product_id:
            product = db.get(ServiceProduct, event.service_product_id)
            if product and not product.is_deleted:
                fee_amount += Decimal(str(product.default_price))
        uninstall_items.append({
            'type': 'taxa_desinstalacao',
            'event_id': event.id,
            'client_id': event.client_id,
            'client_name': client.name if client else f'Cliente #{event.client_id}',
            'client_type': client.type if client else 'pf',
            'vehicle_plate': vehicle.plate if vehicle else None,
            'uninstall_date': event.uninstall_date,
            'fee_amount': float(fee_amount),
            'skipped': fee_amount < MIN_BILLING_AMOUNT,
            'skip_reason': (
                f'Valor R$ {float(fee_amount):.2f} abaixo do mínimo R$ {float(MIN_BILLING_AMOUNT):.2f}'
                if fee_amount < MIN_BILLING_AMOUNT else None
            ),
        })

    # Serviços / cobranças avulsas pendentes (exclui os embutidos)
    charge_items = _pending_charge_items(db, reference_month, exclude_ids=embedded_ids)

    to_generate = [i for i in items if not i['already_generated']]
    already_done = [i for i in items if i['already_generated']]
    # total_amount inclui os serviços embutidos na primeira cobrança
    total_amount = sum(i['total_first_billing'] for i in to_generate)
    total_uninstall = sum(i['fee_amount'] for i in uninstall_items if not i['skipped'])
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
) -> dict:
    simulation = simulate_closure(db, reference_month, filter_type, client_id)
    to_generate = [i for i in simulation['items'] if not i['already_generated']]
    if contract_ids is not None:
        to_generate = [i for i in to_generate if i['contract_id'] in contract_ids]

    created_ids = []
    for item in to_generate:
        contract = db.get(Contract, item['contract_id'])
        if not contract:
            continue
        plan = db.get(Plan, contract.plan_id)
        if not plan:
            continue

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
                vehicle_id=getattr(contract, 'vehicle_id', None),
                tracker_id=getattr(contract, 'tracker_id', None),
                amount=combined_amount,
                due_date=item['due_date'],
                status=BillingStatus.PENDING if item['due_date'] >= date.today() else BillingStatus.OVERDUE,
                period_label=item['period_label'],
                payment_method=contract.payment_method,
                notes=notes,
                title=title,
                billing_type=billing_type,
            )
            db.add(billing)
            db.flush()
            created_ids.append(billing.id)

            # Marca cada serviço embutido como concluído (sem criar billing separado)
            for charge in first_charges:
                charge_obj = db.get(ClientChargeItem, charge['item_id'])
                if charge_obj:
                    charge_obj.active = False
                    charge_obj.completed_at = date.today()
                    charge_obj.status = 'concluido'

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
                vehicle_id=getattr(contract, 'vehicle_id', None),
                tracker_id=getattr(contract, 'tracker_id', None),
                amount=billing_amount,
                due_date=item['due_date'],
                status=BillingStatus.PENDING if item['due_date'] >= date.today() else BillingStatus.OVERDUE,
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
    _por_cliente: dict[int, list[Billing]] = defaultdict(list)
    for bid in created_ids:
        b = db.get(Billing, bid)
        if b and b.billing_type == 'recorrente':
            _por_cliente[b.client_id].append(b)

    for cid, grupo in _por_cliente.items():
        if len(grupo) < 2:
            continue
        cliente = db.get(Client, cid)
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
        unico = Billing(
            client_id=cid,
            billing_type='recorrente',
            title=f'Mensalidades — {len(grupo)} veículos (boleto único)',
            amount=total,
            due_date=venc,
            status=BillingStatus.PENDING if venc >= date.today() else BillingStatus.OVERDUE,
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

    # Processa eventos de desinstalação pendentes
    uninstall_billing_ids = []
    skipped_events = 0
    uninstall_events = _pending_uninstall_events_for_month(db, reference_month)
    now_utc = datetime.now(timezone.utc)

    for event in uninstall_events:
        fee_amount = Decimal(str(event.fee_amount)) if event.fee_amount else Decimal('0')
        fee_title = 'Taxa de desinstalação'
        if event.service_product_id:
            product = db.get(ServiceProduct, event.service_product_id)
            if product and not product.is_deleted:
                fee_amount += Decimal(str(product.default_price))
                fee_title = product.name

        if fee_amount < MIN_BILLING_AMOUNT:
            event.status = 'skipped'
            event.processed_at = now_utc
            skipped_events += 1
            continue

        due_date = _due_date_for_uninstall_event(event, db)
        fee_billing = Billing(
            contract_id=event.contract_id,
            client_id=event.client_id,
            vehicle_id=event.vehicle_id,
            tracker_id=event.tracker_id,
            title=fee_title,
            billing_type='taxa_desinstalacao',
            amount=fee_amount,
            due_date=due_date,
            status=BillingStatus.PENDING if due_date >= date.today() else BillingStatus.OVERDUE,
            period_label=due_date.strftime('%m/%Y'),
            notes=(
                f'Taxa de desinstalação — retirada em {event.uninstall_date.strftime("%d/%m/%Y")}'
                + (f' | {event.notes}' if event.notes else '')
            ),
        )
        db.add(fee_billing)
        db.flush()
        event.status = 'processed'
        event.billing_id = fee_billing.id
        event.processed_at = now_utc
        uninstall_billing_ids.append(fee_billing.id)

    # Gera billings para serviços/cobranças avulsas pendentes (os não embutidos)
    services_generated = 0
    service_billing_ids: list[int] = []
    for charge_item_dict in simulation['charge_items']:
        item_obj = db.get(ClientChargeItem, charge_item_dict['item_id'])
        if item_obj:
            new_billings = generate_item_billings(db, item_obj)
            for b in new_billings:
                db.flush()
                service_billing_ids.append(b.id)
            services_generated += len(new_billings)

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
        'uninstall_fees_skipped': skipped_events,
        'uninstall_billing_ids': uninstall_billing_ids,
        'services_generated': services_generated,
        'service_billing_ids': service_billing_ids,
        'total_services_amount': total_services_amount,
        'grand_total': round(total_mensalidades + total_uninstall_amount + total_services_amount, 2),
    }



# ---------------------------------------------------------------------------
# Relatório de simulação de fechamento (formato do sistema antigo)
# ---------------------------------------------------------------------------
# Texto monoespaçado, agrupado por INTERVENIENTE (quem paga o boleto), com um
# bloco por veículo e uma linha por rastreador — um veículo pode ter mais de um
# equipamento, e cada um tem a própria mensalidade.

_LARGURA = 96          # colunas do relatório
_SEP = '=' * _LARGURA
_EMPRESA = 'MASTERSAT COMERCIO E SERVIÇOS DE RASTREAMENTO LTDA'


def _v(valor: float) -> str:
    """Valor no padrão brasileiro, sem símbolo (o relatório é monoespaçado)."""
    return f'{valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _d(valor: date | None) -> str:
    return valor.strftime('%d/%m/%Y') if valor else '//'


def _par(esquerda: str, direita: str, largura: int = _LARGURA) -> str:
    """Duas colunas na mesma linha: uma à esquerda, outra à direita."""
    espaco = max(1, largura - len(esquerda) - len(direita))
    return f'{esquerda}{" " * espaco}{direita}'


def _total(rotulo: str, valor: float, recuo: int = 40) -> str:
    """
    Linha de total: rótulo recuado e valor à direita.

    Os 12 caracteres finais alinham na MESMA coluna do valor das linhas de
    rastreador (8 + 74 + 12 = 94), senão a coluna de valores fica em degrau.
    """
    corpo = f'{rotulo:<42}{_v(valor):>12}'
    return ' ' * recuo + corpo


def _chave_veiculo(item: dict) -> object:
    """Sem veículo, cada contrato é seu próprio grupo."""
    return item.get('vehicle_id') or f'c{item["contract_id"]}'


def _plural(n: int, singular: str, plural: str) -> str:
    return f'{n} {singular if n == 1 else plural}'


def _no_mes(valor: date | None, mes_ref: str) -> bool:
    """A data cai no mês de referência ('MM/AAAA')?"""
    if not valor or '/' not in (mes_ref or ''):
        return False
    mes, _, ano = mes_ref.partition('/')
    return valor.month == int(mes) and valor.year == int(ano)


def _contagens(itens: list[dict], desinstalacoes: list[dict], mes_ref: str) -> dict[str, int]:
    """Veículos, equipamentos, instalações e desinstalações de um conjunto."""
    return {
        'veiculos': len({_chave_veiculo(i) for i in itens}),
        # Um veículo pode ter mais de um rastreador — por isso a contagem é
        # separada da de veículos (ex.: ACQUE, 1 veículo com 2 equipamentos).
        'equipamentos': len({i['tracker_imei'] for i in itens if i.get('tracker_imei')}),
        'instalacoes': sum(1 for i in itens if _no_mes(i.get('tracker_install_date'), mes_ref)),
        'desinstalacoes': len(desinstalacoes),
    }


_MESES_EXT = ['', 'JANEIRO', 'FEVEREIRO', 'MARÇO', 'ABRIL', 'MAIO', 'JUNHO',
              'JULHO', 'AGOSTO', 'SETEMBRO', 'OUTUBRO', 'NOVEMBRO', 'DEZEMBRO']


def _mes_extenso(mes_ref: str) -> str:
    """'08/2026' → 'AGOSTO/2026'; devolve o original se não bater o formato."""
    mm, _, aaaa = (mes_ref or '').partition('/')
    if aaaa and mm.isdigit() and 1 <= int(mm) <= 12:
        return f'{_MESES_EXT[int(mm)]}/{aaaa}'
    return mes_ref or ''


def _totais_financeiros(itens: list[dict], simulation: dict) -> dict[str, float]:
    """Somatórios do fechamento, por natureza — a mesma conta no topo e no rodapé."""
    mensalidades = sum(float(i.get('billing_amount') or 0) for i in itens)
    produtos = sum(
        float(p.get('amount') or 0)
        for i in itens for p in (i.get('first_month_charges') or [])
    )
    taxas = float(simulation.get('total_uninstall_fees') or 0)
    servicos = float(simulation.get('total_services') or 0)
    return {
        'mensalidades': mensalidades, 'produtos': produtos,
        'taxas': taxas, 'servicos': servicos,
        'geral': mensalidades + produtos + taxas + servicos,
    }


def _painel_totais(contagem: dict[str, int], fin: dict[str, float]) -> list[str]:
    """
    Painel de totais no topo do relatório — a quantidade de tudo antes do
    detalhamento por cliente (pedido de 08/08/2026). Tabela de 5 colunas em
    caixa, no monoespaçado do relatório.
    """
    colunas = [
        ('Veículos', str(contagem['veiculos'])),
        ('Rastreadores', str(contagem['equipamentos'])),
        ('Instalações', str(contagem['instalacoes'])),
        ('Desinstalações', str(contagem['desinstalacoes'])),
        ('Total Geral', f'R$ {_v(fin["geral"])}'),
    ]
    w = 18  # 5 células de 18 + 6 bordas = 96 = _LARGURA
    borda = '+' + '+'.join('-' * w for _ in colunas) + '+'

    def _linha(celulas: list[str]) -> str:
        return '|' + '|'.join(c.center(w) for c in celulas) + '|'

    return [
        borda,
        _linha([rot for rot, _ in colunas]),
        borda,
        _linha([val for _, val in colunas]),
        borda,
    ]


def montar_linhas_simulacao(simulation: dict) -> list[str]:
    """
    Monta o relatório como lista de linhas de texto.

    Separado da geração do PDF para poder ser testado sem abrir o arquivo.
    """
    agora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    mes_ref = simulation.get('reference_month', '')
    titulo = f'PRÉVIA DE FECHAMENTO — {_mes_extenso(mes_ref)}'.strip(' —')
    linhas: list[str] = [_SEP, titulo.center(_LARGURA), _SEP]

    itens = simulation.get('items') or []
    if not itens:
        linhas += ['', 'Nenhum contrato a faturar no período.']
        return linhas

    # 1) agrupa por interveniente  2) dentro dele, por veículo
    por_interveniente: dict[str, list[dict]] = defaultdict(list)
    for item in itens:
        por_interveniente[item.get('interveniente_nome') or item['client_name']].append(item)

    # As desinstalações vêm por cliente; o relatório agrupa por interveniente.
    # Este mapa leva uma à outra para a contagem cair no bloco certo.
    interveniente_do_cliente = {
        i.get('client_id'): (i.get('interveniente_nome') or i['client_name']) for i in itens
    }
    desinst_por_grupo: dict[str, list[dict]] = defaultdict(list)
    for ev in simulation.get('uninstall_events') or []:
        grupo = interveniente_do_cliente.get(ev.get('client_id')) or ev.get('client_name') or ''
        desinst_por_grupo[grupo].append(ev)

    fin = _totais_financeiros(itens, simulation)

    # ── Painel de totais no topo (a quantidade de tudo antes dos clientes) ──
    linhas.append(_par(f'Mês de referência: {mes_ref}', f'Gerado em {agora}'))
    linhas.append('')
    linhas += _painel_totais(
        _contagens(itens, simulation.get('uninstall_events') or [], mes_ref), fin)
    linhas.append('')

    for interveniente, do_grupo in sorted(por_interveniente.items()):
        por_veiculo: dict[object, list[dict]] = defaultdict(list)
        for item in do_grupo:
            por_veiculo[_chave_veiculo(item)].append(item)

        venc = do_grupo[0].get('due_date')
        mes_venc = venc.strftime('%m/%Y') if venc else ''
        qtd = _contagens(do_grupo, desinst_por_grupo.get(interveniente, []), mes_ref)

        linhas.append(f'INTERVENIENTE: {interveniente}')
        linhas.append(f'MATRIZ/FILIAL: {_EMPRESA}')
        linhas.append('')
        linhas.append(_par(
            f'MÊS REFERENTE: {mes_ref}',
            _par(f'MÊS VENCIMENTO: {mes_venc}',
                 f'QUANTIDADE VEÍCULOS: {qtd["veiculos"]}', 62),
        ))
        linhas.append(_par(
            f'QUANTIDADE EQUIPAMENTOS: {qtd["equipamentos"]}',
            _par(f'INSTALAÇÕES NO MÊS: {qtd["instalacoes"]}',
                 f'DESINSTALAÇÕES NO MÊS: {qtd["desinstalacoes"]}', 62),
        ))
        linhas.append('')

        total_grupo = 0.0
        for contratos in por_veiculo.values():
            primeiro = contratos[0]
            venc_v = primeiro.get('due_date')
            dia = primeiro.get('billing_day') or (venc_v.day if venc_v else '')

            linhas.append(f'CLIENTE: {primeiro["client_name"]}')
            linhas.append(_par(
                f'PLACA: {primeiro.get("vehicle_plate") or "—"}',
                f'TIPO VEÍCULO: {(primeiro.get("vehicle_type") or "—").upper()}', 78))
            linhas.append(_par(
                f'DATA CADASTRO: {_d(primeiro.get("vehicle_created_at"))}',
                f'RASTREADOR: {primeiro.get("tracker_imei") or "—"}', 78))
            linhas.append(_par(
                f'DATA CONTRATO: {_d(primeiro.get("contract_start_date"))}',
                f'VENCIMENTO: {dia}[{_d(venc_v)}]', 78))
            linhas.append('')

            total_veiculo = 0.0
            for c in contratos:
                mensalidade = float(c.get('billing_amount') or 0)
                instalacao = _d(c.get('tracker_install_date'))
                rotulo = f'RASTREADOR: DATA INSTALAÇÃO [{instalacao}] - MENSALIDADE {_v(mensalidade)}:'
                linhas.append(' ' * 8 + f'{rotulo:<74}{_v(mensalidade):>12}'.rstrip())

                # Serviços/produtos embutidos na primeira cobrança
                produtos = c.get('first_month_charges') or []
                soma_produtos = 0.0
                for prod in produtos:
                    val = float(prod.get('amount') or 0)
                    soma_produtos += val
                    desc = f'PRODUTO - {str(prod.get("title") or "").upper()}:'
                    linhas.append(' ' * 8 + f'{desc:<74}{_v(val):>12}'.rstrip())
                if produtos:
                    linhas.append(_total('SOMA PRODUTOS:', soma_produtos))

                linhas.append(_total('TOTAL RASTREADOR:', mensalidade))
                linhas.append('')
                total_veiculo += mensalidade + soma_produtos

            linhas.append(_total('TOTAL VEÍCULO:', total_veiculo))
            linhas.append('')
            total_grupo += total_veiculo

        # Sem impostos configurados, os dois totais são iguais — as duas linhas
        # existem porque o relatório de referência as traz.
        linhas.append(_total('TOTAL BOLETO S/ IMPOSTOS:', total_grupo))
        linhas.append(_total('TOTAL BOLETO C/ IMPOSTOS:', total_grupo))
        linhas.append(_SEP)

    linhas += _movimentacao_do_mes(itens, simulation, por_interveniente, mes_ref)
    linhas += _resumo_geral(simulation, itens, por_interveniente, mes_ref, fin)
    return linhas


def _movimentacao_do_mes(itens: list[dict], simulation: dict,
                         por_interveniente: dict[str, list[dict]],
                         mes_ref: str) -> list[str]:
    """
    Instalações e desinstalações do período, uma a uma.

    Pedido da reunião de 07/08/2026: o resumo diz quantas foram; aqui se vê
    quais — por interveniente e por cliente, com data, placa e equipamento.
    """
    interveniente_do_cliente = {
        i.get('client_id'): (i.get('interveniente_nome') or i['client_name']) for i in itens
    }

    instalacoes: dict[str, list[dict]] = defaultdict(list)
    for item in itens:
        if _no_mes(item.get('tracker_install_date'), mes_ref):
            instalacoes[item.get('interveniente_nome') or item['client_name']].append(item)

    desinstalacoes: dict[str, list[dict]] = defaultdict(list)
    for ev in simulation.get('uninstall_events') or []:
        grupo = interveniente_do_cliente.get(ev.get('client_id')) or ev.get('client_name') or ''
        desinstalacoes[grupo].append(ev)

    if not instalacoes and not desinstalacoes:
        return []

    linhas = ['', f'MOVIMENTAÇÃO DO PERÍODO — {mes_ref}'.center(_LARGURA), _SEP]

    for grupo in sorted(set(instalacoes) | set(desinstalacoes)):
        linhas.append(f'INTERVENIENTE: {grupo}')

        for item in sorted(instalacoes.get(grupo, []),
                           key=lambda i: (i.get('tracker_install_date') or date.min,
                                          i.get('vehicle_plate') or '')):
            linhas.append(_par(
                f'    INSTALAÇÃO  {_d(item.get("tracker_install_date"))}  '
                f'{(item.get("vehicle_plate") or "SEM PLACA"):<10} '
                f'RASTREADOR {item.get("tracker_imei") or "—"}',
                item['client_name'][:34],
            ))

        for ev in sorted(desinstalacoes.get(grupo, []),
                         key=lambda e: (e.get('uninstall_date') or date.min,
                                        e.get('vehicle_plate') or '')):
            taxa = float(ev.get('fee_amount') or 0)
            # Taxa abaixo do mínimo não vira cobrança, mas a desinstalação
            # aconteceu — sumir com ela do relatório esconderia o serviço.
            sufixo = 'SEM COBRANÇA' if ev.get('skipped') else f'TAXA {_v(taxa)}'
            linhas.append(_par(
                f'    DESINSTALAÇÃO  {_d(ev.get("uninstall_date"))}  '
                f'{(ev.get("vehicle_plate") or "SEM PLACA"):<10} {sufixo}',
                (ev.get('client_name') or '')[:34],
            ))

        linhas.append('    Subtotal: ' + ' · '.join((
            _plural(len(instalacoes.get(grupo, [])), 'instalação', 'instalações'),
            _plural(len(desinstalacoes.get(grupo, [])), 'desinstalação', 'desinstalações'),
        )))
        linhas.append('')

    linhas.append(_SEP)
    return linhas


def _resumo_geral(simulation: dict, itens: list[dict],
                  por_interveniente: dict[str, list[dict]], mes_ref: str,
                  fin: dict[str, float]) -> list[str]:
    """Fecha o relatório com o consolidado do período (mesma conta do topo)."""
    desinstalacoes = simulation.get('uninstall_events') or []
    qtd = _contagens(itens, desinstalacoes, mes_ref)

    linhas = ['', f'RESUMO DO FECHAMENTO — {mes_ref}'.center(_LARGURA), _SEP]
    linhas.append(_par(
        f'INTERVENIENTES: {len(por_interveniente)}',
        _par(f'VEÍCULOS: {qtd["veiculos"]}',
             f'EQUIPAMENTOS: {qtd["equipamentos"]}', 62),
    ))
    linhas.append(_par(
        f'INSTALAÇÕES NO MÊS: {qtd["instalacoes"]}',
        _par(f'DESINSTALAÇÕES NO MÊS: {qtd["desinstalacoes"]}',
             f'CONTRATOS: {len(itens)}', 62),
    ))

    # Cobranças já geradas continuam listadas no detalhamento; sem a linha, o
    # total do resumo pareceria não bater com o que o fechamento vai gerar.
    ja_geradas = int(simulation.get('already_generated') or 0)
    if ja_geradas:
        linhas.append(f'CONTRATOS JÁ FATURADOS NO PERÍODO (inclusos acima): {ja_geradas}')

    linhas.append('')
    linhas.append(_total('TOTAL MENSALIDADES:', fin['mensalidades']))
    if fin['produtos']:
        linhas.append(_total('TOTAL PRODUTOS/SERVIÇOS NA 1ª COBRANÇA:', fin['produtos']))
    if fin['taxas']:
        linhas.append(_total('TOTAL TAXAS DE DESINSTALAÇÃO:', fin['taxas']))
    if fin['servicos']:
        linhas.append(_total('TOTAL SERVIÇOS AVULSOS:', fin['servicos']))
    linhas.append(_total('TOTAL GERAL:', fin['geral']))
    linhas.append(_SEP)
    return linhas


def generate_closure_pdf(simulation: dict) -> BytesIO:
    """Simulação de fechamento em PDF, no formato monoespaçado do SGR."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    linhas = montar_linhas_simulacao(simulation)

    buffer = BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=A4)
    c.setTitle('Simulação de Fechamento — MasterSat')
    largura, altura = A4
    margem = 10 * mm
    topo = altura - margem
    passo = 3.3 * mm
    fonte = 6.5

    y = topo
    pagina = 1
    for linha in linhas:
        if y < margem + 8 * mm:
            c.setFont('Courier', 6)
            c.drawCentredString(largura / 2, margem, f'Página {pagina}')
            c.showPage()
            pagina += 1
            y = topo
        c.setFont('Courier', fonte)
        c.drawString(margem, y, linha)
        y -= passo

    c.setFont('Courier', 6)
    c.drawCentredString(largura / 2, margem, f'Página {pagina}')
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer
