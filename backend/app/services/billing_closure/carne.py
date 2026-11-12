"""Carnê Sicredi do fechamento: o ano inteiro para quem precisa renovar.

O ciclo é validado por responsável financeiro antes de expandir os contratos.
Uma competência ocupada em qualquer veículo impede renovar só parte do carnê.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.contract import Contract
from app.models.plan import Plan
from app.services.billing_closure.recurring import _billing_due_in_month
from app.services.financial import add_months, existing_recurring_periods, normalize_due_date


def validate_carne_request(forma_cobranca: str | None, installments: int | None) -> None:
    if installments is None:
        return
    if forma_cobranca != 'carne_sicredi' or not 2 <= installments <= 60:
        raise ValueError('Informe de 2 a 60 parcelas com a forma de cobrança Carnê Sicredi.')


def carne_contracts_by_payer(db: Session, payer_ids: set[int], activity_month: date,
                            billing_month: date) -> dict[int, set[int]]:
    """Contratos faturáveis do responsável, inclusive fora do filtro PF/PJ."""
    result = {payer_id: set() for payer_id in payer_ids}
    if not payer_ids:
        return result
    payer = func.coalesce(Contract.interveniente_client_id, Contract.client_id)
    rows = db.execute(select(Contract, Plan, payer).join(Plan, Plan.id == Contract.plan_id).where(
        payer.in_(payer_ids), Contract.is_deleted.is_(False), Contract.status == 'ativo',
        (Contract.end_date.is_(None) | (Contract.end_date >= activity_month)),
    )).all()
    for contract, plan, payer_id in rows:
        if _billing_due_in_month(contract, plan, billing_month) is not None:
            result[payer_id].add(contract.id)
    return result


def expand_carne_items(
    db: Session, items: list[dict], activity_month: date, installments: int,
) -> list[dict]:
    by_payer: dict[int, list[dict]] = {}
    for original in items:
        if not original.get('is_final_prorata'):
            by_payer.setdefault(original['payer_client_id'], []).append(original)
    billing_month = next((i['due_date'].replace(day=1) for i in items
                          if not i.get('is_final_prorata')), add_months(activity_month, 1))
    complete_scope = carne_contracts_by_payer(db, set(by_payer), activity_month, billing_month)
    blocked: dict[int, str] = {}
    candidates_by_contract: dict[int, list[dict]] = {}
    for payer_id, originals in by_payer.items():
        if {i['contract_id'] for i in originals} != complete_scope[payer_id]:
            blocked[payer_id] = (
                'Há contratos do responsável financeiro fora desta prévia. '
                'Confira todos os veículos antes de renovar o carnê.'
            )
            continue
        if any(i['already_generated'] for i in originals):
            blocked[payer_id] = (
                'O primeiro mês do ciclo já está cobrado em um dos contratos do responsável '
                'financeiro. Aguarde o término do carnê e confira todos os veículos.'
            )
            continue
        occupied_details = []
        for original in originals:
            candidates = _contract_candidates(db, original, activity_month, installments)
            candidates_by_contract[original['contract_id']] = candidates
            occupied = existing_recurring_periods(db, original['contract_id'],
                                                 [i['period_label'] for i in candidates])
            if occupied:
                occupied_details.append(f'contrato #{original["contract_id"]}: {", ".join(sorted(occupied))}')
        if occupied_details:
            blocked[payer_id] = (
                'O ciclo do responsável financeiro tem competências já cobradas ('
                + '; '.join(occupied_details)
                + '). Revise o carnê e os contratos; nenhuma parcela deste responsável será gerada.'
            )

    expanded: list[dict] = []
    for original in items:
        # A final uninstall prorata is one last obligation, never a new booklet.
        if original.get('is_final_prorata'):
            expanded.append(original)
        elif reason := blocked.get(original['payer_client_id']):
            expanded.append({**original, 'already_generated': True,
                             'carne_renewal_blocked': True, 'carne_renewal_reason': reason})
        else:
            expanded.extend(candidates_by_contract[original['contract_id']])
    return expanded


def _contract_candidates(db: Session, original: dict, activity_month: date,
                         installments: int) -> list[dict]:
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
            carne_contract_end_date=contract.end_date, carne_plan_id=contract.plan_id,
        )
        if offset:
            item.update(
                billing_amount=original['plan_price'], total_first_billing=original['plan_price'],
                is_prorata=False, prorated_days=0, days_in_month=0, first_month_charges=[],
            )
        candidates.append(item)
    return candidates


def scope_carne_obligations(items: list[dict], uninstall_items: list[dict],
                            charge_items: list[dict]) -> list[dict]:
    """Sem renovação, obrigações avulsas permanecem pendentes para outro fluxo."""
    renewing = {i['payer_client_id'] for i in items if i.get('installment_number')}
    reasons = {i['payer_client_id']: i['carne_renewal_reason']
               for i in items if i.get('carne_renewal_blocked')}
    blocks: dict[int, dict] = {}
    for entry in (*items, *uninstall_items, *charge_items):
        payer_id = entry['payer_client_id']
        if payer_id in renewing:
            continue
        reason = reasons.get(payer_id, 'Nenhum contrato deste responsável está elegível para renovar '
                             'o carnê. As obrigações permanecem pendentes para outro fechamento.')
        entry.update(carne_renewal_blocked=True, carne_renewal_reason=reason)
        if entry.get('type') == 'recorrente':
            entry['already_generated'] = True
        elif entry.get('type') == 'taxa_desinstalacao':
            entry.update(deferred=True, skipped=True, skip_reason=reason)
        block = blocks.setdefault(payer_id, {
            'payer_client_id': payer_id, 'payer_name': entry.get('payer_name'),
            'contract_ids': [], 'reason': reason,
        })
        contract_id = entry.get('contract_id')
        if contract_id is not None and contract_id not in block['contract_ids']:
            block['contract_ids'].append(contract_id)
    return list(blocks.values())
