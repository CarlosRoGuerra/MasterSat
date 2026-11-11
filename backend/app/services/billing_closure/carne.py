"""Monthly installments explicitly requested in the Sicredi closing preview."""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.contract import Contract
from app.models.plan import Plan
from app.services.financial import add_months, existing_recurring_periods, normalize_due_date


def validate_carne_request(forma_cobranca: str | None, installments: int | None) -> None:
    if installments is None:
        return
    if forma_cobranca != 'carne_sicredi' or not 2 <= installments <= 60:
        raise ValueError('Informe de 2 a 60 parcelas com a forma de cobrança Carnê Sicredi.')


def expand_carne_items(
    db: Session, items: list[dict], activity_month: date, installments: int,
) -> list[dict]:
    expanded: list[dict] = []
    for original in items:
        # A final uninstall prorata is one last obligation, never a new booklet.
        if original.get('is_final_prorata'):
            expanded.append(original)
            continue
        contract = db.get(Contract, original['contract_id'])
        plan = db.get(Plan, contract.plan_id)
        if int(plan.billing_interval_months or 1) != 1:
            raise ValueError(f'Contrato #{contract.id}: o carnê exige um plano mensal.')
        candidates = []
        for offset in range(installments):
            service_month = add_months(activity_month, offset)
            if contract.end_date and contract.end_date < service_month:
                break
            due_date = normalize_due_date(original['due_date'], offset, contract.billing_day, 1)
            item = dict(original)
            item.update(
                due_date=due_date, period_label=due_date.strftime('%m/%Y'),
                service_period_label=service_month.strftime('%m/%Y'),
                installment_number=offset + 1, installment_total=installments,
                carne_bank='sicredi',
            )
            if offset:
                item.update(
                    billing_amount=original['plan_price'], total_first_billing=original['plan_price'],
                    is_prorata=False, prorated_days=0, days_in_month=0, first_month_charges=[],
                )
            candidates.append(item)
        occupied = existing_recurring_periods(db, contract.id, [item['period_label'] for item in candidates])
        for item in candidates:
            item['already_generated'] = item['period_label'] in occupied
        expanded.extend(candidates)
    return expanded
