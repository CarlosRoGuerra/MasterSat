"""Vencimento em novembro pertence ao fechamento dos serviços de outubro."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.audit_log import AuditLog
from app.models.billing import Billing
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.closure_job import ClosureJob
from app.models.enums import BillingStatus
from app.services.financial import add_months
from app.services.tracker_charge_recovery import correct_tracker_charge_due_date


def add_item(http, contract, *, due='2026-11-10', amount=5, basis='due_date', installments=1):
    response = http.post('/api/v1/client-charge-items/', json={
        'client_id': contract.client_id, 'contract_id': contract.id,
        'vehicle_id': contract.vehicle_id, 'tracker_id': contract.tracker_id,
        'title': 'Teste', 'quantity': 1, 'unit_price': amount,
        'installment_count': installments, 'start_date': due, 'date_basis': basis,
    })
    assert response.status_code == 200, response.text
    return response.json()['id']


@pytest.mark.parametrize('due', ['2026-11-10', '2027-01-31', '2028-02-29'])
def test_manual_due_date_uses_previous_closure_and_keeps_selected_day(http, db, contrato, due):
    item_id = add_item(http, contrato, due=due)
    service_month = add_months(date.fromisoformat(due).replace(day=1), -1).strftime('%Y-%m')
    params = {'service_month': service_month}
    preview = http.get('/api/v1/billing-closure/simulate', params=params)
    assert preview.status_code == 200, preview.text
    [item] = preview.json()['charge_items']
    assert item['item_id'] == item_id
    assert item['date_basis'] == 'due_date'
    assert item['start_date'] == due
    result = http.post('/api/v1/billing-closure/generate', params={**params, 'charge_item_ids': item_id})
    assert result.status_code == 200, result.text
    [billing_id] = result.json()['payment_billing_ids']
    assert db.get(Billing, billing_id).due_date == date.fromisoformat(due)
    assert db.get(ClosureJob, result.json()['closure_batch_id']).reference_month == service_month
    assert http.get('/api/v1/billing-closure/simulate', params=params).json()['charge_items'] == []


def test_future_due_date_and_real_service_date_do_not_enter_early(http, contrato):
    november = add_item(http, contrato)
    december = add_item(http, contrato, due='2026-12-10')
    actual_october_service = add_item(http, contrato, due='2026-10-29', basis='service_date')
    september = http.get('/api/v1/billing-closure/simulate', params={'service_month': '2026-09'})
    assert september.status_code == 200, september.text
    assert september.json()['charge_items'] == []
    october = http.get('/api/v1/billing-closure/simulate', params={'service_month': '2026-10'})
    assert october.status_code == 200, october.text
    assert {row['item_id'] for row in october.json()['charge_items']} == {november, actual_october_service}
    assert december not in {row['item_id'] for row in october.json()['charge_items']}


def test_installment_schedule_keeps_due_dates_and_generates_one_per_closure(http, db, contrato):
    item_id = add_item(http, contrato, due='2026-11-30', amount=100, installments=3)
    for month, expected_due, expected_amount in [
        ('2026-10', date(2026, 11, 30), Decimal('33.33')),
        ('2026-11', date(2026, 12, 30), Decimal('33.33')),
        ('2026-12', date(2027, 1, 30), Decimal('33.34')),
    ]:
        params = {'service_month': month}
        result = http.post('/api/v1/billing-closure/generate', params={**params, 'charge_item_ids': item_id})
        assert result.status_code == 200, result.text
        [billing_id] = result.json()['payment_billing_ids']
        billing = db.get(Billing, billing_id)
        assert (billing.due_date, billing.amount) == (expected_due, expected_amount)
        again = http.get('/api/v1/billing-closure/simulate', params=params)
        assert again.status_code == 200, again.text
        assert again.json()['charge_items'] == []


def test_corrects_existing_five_real_item_from_november_into_october(http, db, contrato):
    # A mensalidade de novembro já foi gerada; o adicional ainda deve aparecer
    # no mesmo fechamento de outubro, como no caso mostrado pelo operador.
    db.add(Billing(client_id=contrato.client_id, payer_client_id=contrato.client_id,
        contract_id=contrato.id, vehicle_id=contrato.vehicle_id, tracker_id=contrato.tracker_id,
        billing_type='recorrente', title='Mensalidade', amount=Decimal('99.90'),
        due_date=date(2026, 11, 15), period_label='11/2026', status=BillingStatus.PENDING))
    db.commit()
    item_id = add_item(http, contrato, basis='service_date')
    params = {'service_month': '2026-10'}
    assert http.get('/api/v1/billing-closure/simulate', params=params).json()['charge_items'] == []
    kwargs = dict(client_id=contrato.client_id, service_month=date(2026, 10, 1),
        due_date=date(2026, 11, 10), amount=Decimal('5.00'))
    preview = correct_tracker_charge_due_date(db, **kwargs)
    assert preview['result'] == 'pronto_para_corrigir'
    assert db.get(ClientChargeItem, item_id).date_basis == 'service_date'
    result = correct_tracker_charge_due_date(db, **kwargs, apply=True)
    db.commit()
    assert result['item_id'] == item_id
    assert db.query(ClientChargeItem).count() == 1
    assert db.get(ClientChargeItem, item_id).start_date == date(2026, 11, 10)
    simulation = http.get('/api/v1/billing-closure/simulate', params=params)
    assert simulation.status_code == 200, simulation.text
    assert simulation.json()['to_generate'] == 0
    assert [row['item_id'] for row in simulation.json()['charge_items']] == [item_id]
    assert simulation.json()['grand_total'] == 5
    generated = http.post('/api/v1/billing-closure/generate', params={**params, 'charge_item_ids': item_id})
    assert generated.status_code == 200, generated.text
    [billing_id] = generated.json()['payment_billing_ids']
    assert db.get(Billing, billing_id).due_date == date(2026, 11, 10)
    assert correct_tracker_charge_due_date(db, **kwargs, apply=True)['result'] == 'ja_corrigido'
    assert db.query(AuditLog).filter(AuditLog.entity_type == 'client_charge_item').count() == 1


def test_correction_refuses_ambiguous_or_billed_items(http, db, contrato):
    item_id = add_item(http, contrato, basis='service_date')
    kwargs = dict(client_id=contrato.client_id, service_month=date(2026, 10, 1),
        due_date=date(2026, 11, 10), amount=Decimal('5.00'), apply=True)
    duplicate_id = add_item(http, contrato, basis='service_date')
    with pytest.raises(ValueError, match='único adicional'):
        correct_tracker_charge_due_date(db, **kwargs)
    duplicate = db.get(ClientChargeItem, duplicate_id)
    duplicate.is_deleted = True
    db.add(Billing(client_id=contrato.client_id, contract_id=contrato.id, item_id=item_id,
        billing_type='item', amount=5, due_date=date(2026, 12, 10), status=BillingStatus.PENDING))
    db.commit()
    with pytest.raises(ValueError, match='faturado'):
        correct_tracker_charge_due_date(db, **kwargs)
    assert db.get(ClientChargeItem, item_id).date_basis == 'service_date'


def test_cli_corrects_selected_existing_item(http, db, contrato, monkeypatch, capsys):
    from scripts import recuperar_cobrancas_rastreador as command
    item_id = add_item(http, contrato, basis='service_date')
    monkeypatch.setattr(command, 'SessionLocal', sessionmaker(bind=db.get_bind(), autoflush=False))
    client = db.get(Client, contrato.client_id)
    args = ['--cliente', client.name, '--mes-servico', '2026-10',
        '--adicional-vencimento', '2026-11-10', '--valor', '5.00', '--aplicar']
    assert command.main(args) == 0
    assert 'corrigido' in capsys.readouterr().out
    db.expire_all()
    assert db.get(ClientChargeItem, item_id).date_basis == 'due_date'
    assert command.main(args) == 0
    assert 'ja_corrigido' in capsys.readouterr().out


def test_editor_can_correct_pending_date_but_cannot_reinterpret_billed_item(http, db, contrato):
    item_id = add_item(http, contrato, basis='service_date')
    updated = http.put(f'/api/v1/client-charge-items/{item_id}', json={'date_basis': 'due_date'})
    assert updated.status_code == 200, updated.text
    assert updated.json()['start_date'] == '2026-11-10'
    assert updated.json()['date_basis'] == 'due_date'
    assert http.put(f'/api/v1/client-charge-items/{item_id}', json={'date_basis': None}).status_code == 422
    generated = http.post('/api/v1/billing-closure/generate', params={
        'service_month': '2026-10', 'charge_item_ids': item_id})
    assert generated.status_code == 200, generated.text
    refused = http.put(f'/api/v1/client-charge-items/{item_id}', json={'date_basis': 'service_date'})
    assert refused.status_code == 409, refused.text
    assert db.get(ClientChargeItem, item_id).date_basis == 'due_date'
