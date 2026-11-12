"""Renovação do carnê é integral por responsável, sem completar meses soltos."""
from datetime import date
from decimal import Decimal

import pytest

from app.models.billing import Billing
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.models.uninstall_event import UninstallEvent

PREFIX = '/api/v1/billing-closure'
PARAMS = {'service_month': '2026-11', 'forma_cobranca': 'carne_sicredi', 'carne_installments': 12}


def _contract(db, client, plan, *, payer_id=None):
    client.forma_cobranca = 'carne_sicredi'
    contract = Contract(client_id=client.id, interveniente_client_id=payer_id, plan_id=plan.id,
                        start_date=date(2026, 1, 1), status='ativo', billing_day=10)
    db.add(contract)
    db.commit()
    return contract


def _occupied(db, contract, due=date(2026, 12, 10)):
    billing = Billing(contract_id=contract.id, client_id=contract.client_id,
                      amount=Decimal('99.90'), due_date=due, period_label=due.strftime('%m/%Y'),
                      billing_type='carne', status=BillingStatus.PENDING)
    db.add(billing)
    db.commit()
    return billing


def test_first_month_occupied_blocks_the_whole_cycle(http, db, cliente, plan):
    contract = _contract(db, cliente, plan)
    existing = _occupied(db, contract)
    preview = http.get(PREFIX + '/simulate', params=PARAMS).json()
    assert preview['to_generate'] == 0
    assert preview['items'][0]['carne_renewal_blocked']
    assert 'primeiro mês' in preview['items'][0]['carne_renewal_reason']
    assert preview['carne_renewal_blocks'][0]['contract_ids'] == [contract.id]
    result = http.post(PREFIX + '/generate', params=PARAMS).json()
    assert result['payment_billing_ids'] == []
    assert db.query(Billing).count() == 1
    assert db.get(Billing, existing.id).status == BillingStatus.PENDING


def test_ongoing_cycle_waits_and_renews_after_its_twelfth_month(http, db, cliente, plan):
    _contract(db, cliente, plan)
    first = http.post(PREFIX + '/generate', params=PARAMS)
    assert first.status_code == 200, first.text
    assert first.json()['payment_titles_generated'] == 12
    following = {**PARAMS, 'service_month': '2026-12'}
    assert http.get(PREFIX + '/simulate', params=following).json()['to_generate'] == 0
    assert http.post(PREFIX + '/generate', params=following).json()['payment_titles_generated'] == 0
    renewal = {**PARAMS, 'service_month': '2027-11'}
    assert http.get(PREFIX + '/simulate', params=renewal).json()['to_generate'] == 12
    result = http.post(PREFIX + '/generate', params=renewal)
    assert result.status_code == 200, result.text
    titles = [db.get(Billing, bid) for bid in result.json()['payment_billing_ids']]
    assert len(titles) == 12
    assert min(b.due_date for b in titles) == date(2027, 12, 10)
    assert max(b.due_date for b in titles) == date(2028, 11, 10)
    assert db.query(Billing).count() == 24


@pytest.mark.parametrize('due', [date(2026, 12, 10), date(2027, 2, 10)])
def test_one_covered_vehicle_blocks_all_vehicles_and_keeps_extras_pending(
    http, db, cliente, plan, veiculo, due,
):
    covered = _contract(db, cliente, plan)
    new_vehicle = _contract(db, cliente, plan)
    existing = _occupied(db, covered, due)
    service = ClientChargeItem(client_id=cliente.id, title='Serviço pendente', quantity=1,
                               unit_price=Decimal('10'), total_amount=Decimal('10'),
                               installment_count=1, start_date=date(2026, 11, 1), active=True)
    fee = UninstallEvent(client_id=cliente.id, vehicle_id=veiculo.id, contract_id=covered.id,
                         uninstall_date=date(2026, 11, 1), fee_amount=Decimal('15'), status='pending')
    db.add_all([service, fee])
    db.commit()
    preview = http.get(PREFIX + '/simulate', params=PARAMS).json()
    assert preview['to_generate'] == 0 and preview['grand_total'] == 0
    assert {i['contract_id'] for i in preview['items']} == {covered.id, new_vehicle.id}
    assert all(i['carne_renewal_blocked'] and i['already_generated'] for i in preview['items'])
    assert preview['charge_items'][0]['carne_renewal_blocked']
    assert preview['uninstall_events'][0]['carne_renewal_blocked']
    result = http.post(PREFIX + '/generate', params=PARAMS)
    assert result.status_code == 200, result.text
    assert result.json()['payment_billing_ids'] == []
    assert db.query(Billing).count() == 1
    assert db.get(Billing, existing.id).status == BillingStatus.PENDING
    db.refresh(service)
    db.refresh(fee)
    assert service.active and service.status == 'ativo'
    assert fee.status == 'pending' and fee.billing_id is None


def test_blocked_payer_does_not_prevent_another_payers_full_renewal(http, db, cliente, outro_cliente, plan):
    covered = _contract(db, cliente, plan)
    free = _contract(db, outro_cliente, plan)
    _occupied(db, covered, date(2027, 2, 10))
    preview = http.get(PREFIX + '/simulate', params=PARAMS).json()
    assert preview['to_generate'] == 12
    assert {i['contract_id'] for i in preview['items'] if not i['already_generated']} == {free.id}
    result = http.post(PREFIX + '/generate', params=PARAMS)
    assert result.status_code == 200, result.text
    titles = [db.get(Billing, bid) for bid in result.json()['payment_billing_ids']]
    assert len(titles) == 12 and {b.contract_id for b in titles} == {free.id}


def test_contract_selection_cannot_renew_just_one_vehicle(http, db, cliente, plan):
    first = _contract(db, cliente, plan)
    _contract(db, cliente, plan)
    response = http.post(PREFIX + '/generate', params={**PARAMS, 'contract_ids': first.id})
    assert response.status_code == 409, response.text
    assert 'todos os contratos' in response.json()['detail']
    assert db.query(Billing).count() == 0


def test_pf_filter_cannot_renew_only_part_of_a_shared_payer(http, db, cliente, outro_cliente, plan):
    _contract(db, cliente, plan)
    outro_cliente.type = 'pj'
    _contract(db, outro_cliente, plan, payer_id=cliente.id)
    params = {**PARAMS, 'filter_type': 'pf'}
    preview = http.get(PREFIX + '/simulate', params=params).json()
    assert preview['to_generate'] == 0
    assert 'fora desta prévia' in preview['items'][0]['carne_renewal_reason']
    assert http.post(PREFIX + '/generate', params=params).json()['payment_billing_ids'] == []


def test_selection_of_only_a_service_does_not_create_a_one_title_booklet(http, db, cliente, plan):
    _contract(db, cliente, plan)
    service = ClientChargeItem(client_id=cliente.id, title='Serviço pendente', quantity=1,
                               unit_price=Decimal('10'), total_amount=Decimal('10'),
                               installment_count=1, start_date=date(2026, 11, 1), active=True)
    db.add(service)
    db.commit()
    result = http.post(PREFIX + '/generate', params={**PARAMS, 'charge_item_ids': service.id})
    assert result.status_code == 200, result.text
    assert result.json()['payment_billing_ids'] == []
    db.refresh(service)
    assert service.active and db.query(Billing).count() == 0


def test_new_contract_during_generation_invalidates_the_payers_snapshot(
    http, db, cliente, plan, monkeypatch,
):
    from app.services import billing_closure

    _contract(db, cliente, plan)
    lock = billing_closure._locked_contracts

    def concurrent_contract(session, ids):
        session.add(Contract(client_id=cliente.id, plan_id=plan.id, start_date=date(2026, 1, 1),
                             status='ativo', billing_day=10))
        session.flush()
        return lock(session, ids)

    monkeypatch.setattr(billing_closure, '_locked_contracts', concurrent_contract)
    response = http.post(PREFIX + '/generate', params=PARAMS)
    assert response.status_code == 409, response.text
    assert 'Refaça a simulação' in response.json()['detail']
    assert db.query(Billing).count() == 0


@pytest.mark.parametrize('changed_field', ['end_date', 'billing_day', 'price', 'forma_cobranca'])
def test_cycle_changes_under_lock_fail_before_creating_any_installment(
    http, db, cliente, plan, monkeypatch, changed_field,
):
    from app.services import billing_closure

    contract = _contract(db, cliente, plan)
    contract.end_date = date(2027, 2, 28)
    db.commit()
    lock = billing_closure._locked_contracts

    def concurrent_change(session, ids):
        if changed_field == 'end_date':
            contract.end_date = None
        elif changed_field == 'billing_day':
            contract.billing_day = 20
        elif changed_field == 'price':
            plan.price = Decimal('129.90')
        else:
            cliente.forma_cobranca = 'boleto_mensal'
        session.flush()
        return lock(session, ids)

    monkeypatch.setattr(billing_closure, '_locked_contracts', concurrent_change)
    response = http.post(PREFIX + '/generate', params=PARAMS)
    assert response.status_code == 409, response.text
    assert 'Refaça a simulação' in response.json()['detail']
    assert db.query(Billing).count() == 0
