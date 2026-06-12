from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

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

    query = db.query(Contract, Client, Plan, Vehicle, Tracker).join(
        Client, Client.id == Contract.client_id
    ).join(
        Plan, Plan.id == Contract.plan_id
    ).outerjoin(
        Vehicle, Vehicle.id == Contract.vehicle_id
    ).outerjoin(
        Tracker, Tracker.id == Contract.tracker_id
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
    for contract, client, plan, vehicle, tracker in query.all():
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
                plan_label = f'Plano {plan.name} pró-rata {item["prorated_days"]} dias'
            else:
                plan_label = f'Plano {plan.name}'

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
                title = f'Plano {plan.name} — pró-rata {item["prorated_days"]} dias'
                notes = (
                    f'Pró-rata: {item["prorated_days"]} de {item["days_in_month"]} dias'
                    f' — {item["period_label"]}'
                )
                billing_type = 'prorata'
            else:
                title = f'Plano {plan.name}'
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
        'total_amount': total_mensalidades,
        'uninstall_fees_generated': len(uninstall_billing_ids),
        'uninstall_fees_skipped': skipped_events,
        'uninstall_billing_ids': uninstall_billing_ids,
        'services_generated': services_generated,
        'service_billing_ids': service_billing_ids,
        'total_services_amount': total_services_amount,
        'grand_total': round(total_mensalidades + total_uninstall_amount + total_services_amount, 2),
    }


def generate_closure_pdf(simulation: dict) -> BytesIO:
    import os
    from datetime import datetime as _dt
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.lib import colors

    # ── Brand colours ──────────────────────────────────────────────────────
    C_YELLOW   = colors.HexColor('#F0A500')
    C_BLACK    = colors.HexColor('#1A1A1A')
    C_DARK     = colors.HexColor('#2D2D2D')
    C_WHITE    = colors.white
    C_LY       = colors.HexColor('#FFF8E7')   # light yellow row
    C_GRAY     = colors.HexColor('#E8E8E8')

    PAGE      = landscape(A4)   # 297 × 210 mm
    W, H      = PAGE
    LM = 15 * mm
    RM = 15 * mm
    CW = W - LM - RM            # ≈ 267 mm usable
    NOW = _dt.now().strftime('%d/%m/%Y  %H:%M')
    # Absolute path regardless of working directory
    _here = os.path.abspath(os.path.dirname(__file__))
    LOGO = os.path.normpath(os.path.join(_here, '..', '..', '..', 'logotipo.png'))

    buffer = BytesIO()

    # ── Paragraph styles ───────────────────────────────────────────────────
    ps_cell = ParagraphStyle('cell', fontName='Helvetica', fontSize=7.5,
                              textColor=C_DARK, leading=10, alignment=TA_LEFT)
    ps_cell_bold = ParagraphStyle('cellb', fontName='Helvetica-Bold', fontSize=7.5,
                                   textColor=C_DARK, leading=10)
    ps_comp = ParagraphStyle('comp', fontName='Helvetica', fontSize=7,
                              textColor=C_DARK, leading=9.5, alignment=TA_LEFT)

    # ── Header / footer drawn on every page ────────────────────────────────
    def _header_footer(canvas, doc):
        canvas.saveState()

        # Yellow header band (28 mm) — shorter for landscape
        canvas.setFillColor(C_YELLOW)
        canvas.rect(0, H - 28 * mm, W, 28 * mm, fill=1, stroke=0)

        # Logo (left)
        if os.path.exists(LOGO):
            try:
                canvas.drawImage(LOGO, LM, H - 26 * mm,
                                 width=55 * mm, height=22 * mm,
                                 preserveAspectRatio=True, mask='auto')
            except Exception:
                pass

        # Title + info (right)
        canvas.setFillColor(C_BLACK)
        canvas.setFont('Helvetica-Bold', 14)
        canvas.drawRightString(W - RM, H - 13 * mm, 'Simulação de Fechamento')
        canvas.setFont('Helvetica', 8.5)
        canvas.drawRightString(W - RM, H - 20 * mm,
                               f'Período: {simulation["reference_month"]}   •   Gerado em: {NOW}')

        # Separator line
        canvas.setStrokeColor(C_BLACK)
        canvas.setLineWidth(2)
        canvas.line(0, H - 28 * mm, W, H - 28 * mm)

        # Dark footer band (10 mm)
        canvas.setFillColor(C_DARK)
        canvas.rect(0, 0, W, 10 * mm, fill=1, stroke=0)
        canvas.setFillColor(C_WHITE)
        canvas.setFont('Helvetica', 7)
        canvas.drawString(LM, 3.8 * mm,
                          'MasterSat Rastreamento  ·  Solução completa em Rastreamento')
        canvas.drawRightString(W - RM, 3.8 * mm, f'Página {doc.page}')

        canvas.restoreState()

    # ── Document ───────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        buffer, pagesize=PAGE,
        leftMargin=LM, rightMargin=RM,
        topMargin=34 * mm,   # clears 28mm header + gap
        bottomMargin=15 * mm,
    )

    story: list = []

    # ── KPI summary bar ────────────────────────────────────────────────────
    kpi_vals = [
        ('Contratos', str(simulation['total_contracts'])),
        ('A gerar',   str(simulation['to_generate'])),
        ('Mensalidades', f'R$ {simulation["total_amount"]:.2f}'),
        ('Taxas retirada', f'R$ {simulation.get("total_uninstall_fees", 0):.2f}'),
        ('TOTAL GERAL', f'R$ {simulation.get("grand_total", simulation["total_amount"]):.2f}'),
    ]
    kpi_row_labels = [[k for k, _ in kpi_vals]]
    kpi_row_values = [[v for _, v in kpi_vals]]
    kpi_t = Table(kpi_row_labels + kpi_row_values,
                  colWidths=[CW / 5] * 5, rowHeights=[5.5 * mm, 8 * mm])
    kpi_t.setStyle(TableStyle([
        # Label row
        ('BACKGROUND',   (0, 0), (-1, 0), C_DARK),
        ('TEXTCOLOR',    (0, 0), (-1, 0), colors.HexColor('#AAAAAA')),
        ('FONTNAME',     (0, 0), (-1, 0), 'Helvetica'),
        ('FONTSIZE',     (0, 0), (-1, 0), 6.5),
        ('ALIGN',        (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN',       (0, 0), (-1, 0), 'MIDDLE'),
        # Value row
        ('BACKGROUND',   (0, 1), (-1, 1), C_BLACK),
        ('TEXTCOLOR',    (0, 1), (-1, 1), C_WHITE),
        ('FONTNAME',     (0, 1), (-4, 1), 'Helvetica-Bold'),
        ('FONTSIZE',     (0, 1), (-1, 1), 9),
        ('ALIGN',        (0, 1), (-1, 1), 'CENTER'),
        ('VALIGN',       (0, 1), (-1, 1), 'MIDDLE'),
        # Highlight total geral in yellow
        ('BACKGROUND',   (-1, 0), (-1, 1), C_YELLOW),
        ('TEXTCOLOR',    (-1, 0), (-1, 0), C_BLACK),
        ('TEXTCOLOR',    (-1, 1), (-1, 1), C_BLACK),
        ('FONTNAME',     (-1, 1), (-1, 1), 'Helvetica-Bold'),
        # Dividers
        ('LINEAFTER',    (0, 0), (-2, 1), 0.5, colors.HexColor('#444444')),
        ('LINEBELOW',    (0, 1), (-1, 1), 2, C_YELLOW),
        ('TOPPADDING',   (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 2),
    ]))
    story.append(kpi_t)
    story.append(Spacer(1, 5 * mm))

    # ── Helpers ────────────────────────────────────────────────────────────
    def section_bar(label: str):
        t = Table([[label]], colWidths=[CW], rowHeights=[7 * mm])
        t.setStyle(TableStyle([
            ('BACKGROUND',   (0, 0), (-1, -1), C_BLACK),
            ('TEXTCOLOR',    (0, 0), (-1, -1), C_YELLOW),
            ('FONTNAME',     (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE',     (0, 0), (-1, -1), 9),
            ('LEFTPADDING',  (0, 0), (-1, -1), 8),
            ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        return t

    def data_table(header: list, rows: list, widths: list) -> Table:
        t = Table([header] + rows, colWidths=widths, repeatRows=1)
        cmds = [
            ('BACKGROUND',    (0, 0), (-1, 0), C_DARK),
            ('TEXTCOLOR',     (0, 0), (-1, 0), C_WHITE),
            ('FONTNAME',      (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',      (0, 0), (-1, -1), 7.5),
            ('ALIGN',         (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('LINEBELOW',     (0, 0), (-1, 0), 2, C_YELLOW),
            ('GRID',          (0, 0), (-1, -1), 0.25, C_GRAY),
            ('TOPPADDING',    (0, 0), (-1, -1), 3.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
            ('LEFTPADDING',   (0, 0), (-1, -1), 5),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
        ]
        for i in range(1, len(rows) + 1):
            bg = C_LY if i % 2 == 0 else C_WHITE
            cmds.append(('BACKGROUND', (0, i), (-1, i), bg))
        t.setStyle(TableStyle(cmds))
        return t

    def fmt_due(d):
        return d.strftime('%d/%m/%Y') if hasattr(d, 'strftime') else str(d)

    # ── Mensalidades ───────────────────────────────────────────────────────
    if simulation['items']:
        story.append(section_bar('  MENSALIDADES RECORRENTES'))
        story.append(Spacer(1, 1.5 * mm))
        hdr = ['CLIENTE', 'TIPO', 'VEÍCULO', 'PLANO', 'VENCIMENTO', 'COMPOSIÇÃO DA COBRANÇA', 'VALOR (R$)', 'STATUS']
        rows = []
        for item in simulation['items']:
            charges = item.get('first_month_charges', [])
            if item.get('is_prorata') and not charges:
                comp_lines = [f'Pró-rata {item["prorated_days"]}/{item["days_in_month"]} dias']
            elif charges:
                comp_lines = [f'Mensalidade: R$ {item["billing_amount"]:.2f}']
                comp_lines += [f'+ {c["title"]}: R$ {c["amount"]:.2f}' for c in charges]
            else:
                comp_lines = ['Mensalidade integral']
            comp_para = Paragraph('<br/>'.join(comp_lines), ps_comp)
            status = 'Gerado' if item['already_generated'] else 'A gerar'
            rows.append([
                Paragraph(item['client_name'], ps_cell),
                'PJ' if item['client_type'] == 'pj' else 'PF',
                item['vehicle_plate'] or '—',
                Paragraph(item['plan_name'], ps_cell),
                fmt_due(item['due_date']),
                comp_para,
                f'{item["total_first_billing"]:.2f}',
                status,
            ])
        # Landscape widths — total ≈ 267 mm
        story.append(data_table(hdr, rows,
            [55*mm, 9*mm, 22*mm, 26*mm, 20*mm, 85*mm, 22*mm, 16*mm]))
        story.append(Spacer(1, 5 * mm))

    # ── Desinstalações ─────────────────────────────────────────────────────
    if simulation.get('uninstall_events'):
        story.append(section_bar('  TAXAS DE DESINSTALAÇÃO'))
        story.append(Spacer(1, 1.5 * mm))
        hdr = ['CLIENTE', 'TIPO', 'VEÍCULO', 'DATA RETIRADA', 'VALOR (R$)', 'STATUS']
        rows = []
        for item in simulation['uninstall_events']:
            rows.append([
                Paragraph(item['client_name'], ps_cell),
                'PJ' if item['client_type'] == 'pj' else 'PF',
                item['vehicle_plate'] or '—',
                fmt_due(item['uninstall_date']),
                f'{item["fee_amount"]:.2f}',
                item.get('skip_reason') or 'A gerar',
            ])
        story.append(data_table(hdr, rows,
            [90*mm, 11*mm, 30*mm, 30*mm, 24*mm, 60*mm]))
        story.append(Spacer(1, 5 * mm))

    # ── Serviços avulsos ───────────────────────────────────────────────────
    if simulation.get('charge_items'):
        story.append(section_bar('  SERVIÇOS E COBRANÇAS AVULSAS'))
        story.append(Spacer(1, 1.5 * mm))
        hdr = ['CLIENTE', 'TIPO', 'VEÍCULO', 'TÍTULO', 'PARCELAS', 'VALOR/PARCELA', 'TOTAL (R$)']
        rows = []
        for item in simulation['charge_items']:
            rows.append([
                Paragraph(item['client_name'], ps_cell),
                'PJ' if item['client_type'] == 'pj' else 'PF',
                item['vehicle_plate'] or '—',
                Paragraph(item['title'], ps_cell),
                f'{item["generated_count"] + 1}–{item["installment_count"]}',
                f'{item["per_installment_amount"]:.2f}',
                f'{item["total_remaining"]:.2f}',
            ])
        story.append(data_table(hdr, rows,
            [70*mm, 10*mm, 26*mm, 60*mm, 18*mm, 26*mm, 24*mm]))

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    buffer.seek(0)
    return buffer
