from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased

from app.core.timezone import hoje
from app.models.billing import Billing
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.models.plan import Plan
from app.models.tracker import Tracker
from app.models.uninstall_event import UninstallEvent
from app.models.vehicle import Vehicle
from app.services.billing_closure.charge_items import _pending_charge_items
from app.services.billing_closure.recurring import (
    _billing_due_in_month,
    _first_cycle_charge_items,
    _locked_contracts,
    _prorata_fields,
    _validate_contract_relationships,
    _validate_locked_contract_for_closure,
)
from app.services.billing_closure.shared import _lock_competencia
from app.services.billing_closure.uninstall_fees import (
    _due_date_for_uninstall_event,
    _pending_uninstall_events_for_month,
    _uninstall_event_payer_client_id,
    uninstall_fee_for_event,
)
from app.services.financial import (
    _quantize_amount,
    add_months,
    associate_billing_charge_item,
    contract_payer_client_id,
    decimal_to_float,
    generate_item_billings,
    plan_title,
    period_label_for_date,
    refresh_overdue_statuses,
)

MIN_BILLING_AMOUNT = Decimal('5.00')


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
# Relatório de simulação de fechamento — em billing_closure_report.py (BE-03):
# não toca em banco, só transforma o dict de simulate_closure em texto/XLSX/PDF.
# Reexportado aqui porque api/v1/endpoints/billing_closure.py e os testes de
# serviço importam esses nomes de app.services.billing_closure.
# ---------------------------------------------------------------------------
from app.services.billing_closure_report import (  # noqa: F401,E402
    generate_closure_pdf,
    generate_closure_xlsx,
    montar_linhas_simulacao,
)
