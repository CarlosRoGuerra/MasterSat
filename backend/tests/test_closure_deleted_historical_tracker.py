from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.billing import Billing
from app.models.enums import TrackerStatus
from app.models.uninstall_event import UninstallEvent


PREFIX = '/api/v1/billing-closure'


@pytest.fixture()
def final_contract(db, contrato, plan, rastreador_instalado):
    plan.price = Decimal('64.99')
    contrato.status = 'cancelado'
    contrato.end_date = date(2026, 10, 9)
    contrato.uninstalled_at = date(2026, 10, 9)
    rastreador_instalado.status = TrackerStatus.LOST
    rastreador_instalado.client_id = None
    rastreador_instalado.vehicle_id = None
    rastreador_instalado.uninstall_date = contrato.uninstalled_at
    db.commit()
    return contrato


@pytest.mark.parametrize('delete_before_preview', [True, False])
def test_final_closure_keeps_prorata_fee_and_history_after_tracker_deletion(
    http, db, final_contract, rastreador_instalado, delete_before_preview,
):
    tracker = rastreador_instalado
    event = UninstallEvent(
        contract_id=final_contract.id,
        tracker_id=tracker.id,
        vehicle_id=final_contract.vehicle_id,
        client_id=final_contract.client_id,
        uninstall_date=final_contract.uninstalled_at,
        fee_amount=Decimal('160.00'),
        status='pending',
    )
    db.add(event)
    db.commit()
    if delete_before_preview:
        deleted = http.delete(f'/api/v1/trackers/{tracker.id}')
        assert deleted.status_code == 200, deleted.text

    params = {'service_month': '2026-10'}
    preview = http.get(PREFIX + '/simulate', params=params)
    assert preview.status_code == 200, preview.text
    data = preview.json()
    item = next(i for i in data['items'] if i['contract_id'] == final_contract.id)
    assert item['is_final_prorata'] is True
    assert item['prorated_days'] == 9
    assert item['billing_amount'] == 18.87
    assert item['due_date'] == '2026-11-15'
    assert data['grand_total'] == 178.87
    assert [e['event_id'] for e in data['uninstall_events']] == [event.id]

    if not delete_before_preview:
        deleted = http.delete(f'/api/v1/trackers/{tracker.id}')
        assert deleted.status_code == 200, deleted.text

    generation_params = {
        **params,
        'contract_ids': final_contract.id,
        'uninstall_event_ids': event.id,
    }
    generated = http.post(PREFIX + '/generate', params=generation_params)
    assert generated.status_code == 200, generated.text
    result = generated.json()
    assert result['generated'] == 1
    assert result['uninstall_events_processed'] == 1
    assert result['grand_total'] == 178.87
    monthly = db.scalar(select(Billing).where(
        Billing.contract_id == final_contract.id,
        Billing.billing_type == 'prorata',
    ))
    assert monthly.amount == Decimal('18.87')
    assert monthly.due_date == date(2026, 11, 15)
    parent = db.get(Billing, result['payment_billing_ids'][0])
    assert parent.amount == Decimal('178.87')
    db.refresh(event)
    assert event.status == 'processed'
    assert event.billing_id is not None

    repeated = http.post(PREFIX + '/generate', params=generation_params)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()['generated'] == 0
    assert repeated.json()['uninstall_events_processed'] == 0
    assert repeated.json()['payment_billing_ids'] == []
    db.refresh(final_contract)
    db.refresh(tracker)
    assert final_contract.status == 'cancelado'
    assert final_contract.tracker_id == tracker.id
    assert tracker.is_deleted is True
    assert tracker.status == TrackerStatus.LOST


@pytest.mark.parametrize('deleted_relation', ['client', 'plan', 'vehicle', 'payer'])
def test_final_prorata_still_rejects_deleted_financial_relations(
    http, db, final_contract, cliente, plan, veiculo, outro_cliente, deleted_relation,
):
    final_contract.interveniente_client_id = outro_cliente.id
    relations = {'client': cliente, 'plan': plan, 'vehicle': veiculo, 'payer': outro_cliente}
    relations[deleted_relation].is_deleted = True
    db.commit()

    response = http.get(PREFIX + '/simulate', params={'service_month': '2026-10'})

    assert response.status_code == 409, response.text
    assert db.query(Billing).count() == 0
