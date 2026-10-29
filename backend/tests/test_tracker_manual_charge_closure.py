"""Cobrança manual do rastreador deve entrar no fechamento, sem faturar antes."""
from datetime import date
from decimal import Decimal

import pytest

from app.models.billing import Billing
from app.models.client_charge_item import ClientChargeItem


def _add_manual_charge(http, tracker, contract_id=None, start_date='2026-10-29'):
    response = http.post('/api/v1/client-charge-items/', json={
        'client_id': tracker.client_id,
        'vehicle_id': tracker.vehicle_id,
        'tracker_id': tracker.id,
        'contract_id': contract_id,
        'title': 'Manutenção do rastreador',
        'quantity': 1,
        'unit_price': 120.50,
        'installment_count': 1,
        'start_date': start_date,
        'description': 'Acertado com o cliente',
    })
    assert response.status_code == 200, response.text
    return response.json()['id']


@pytest.mark.parametrize('with_payer', [False, True])
def test_manual_charge_joins_monthly_closure_once(
    http, db, contrato, rastreador_instalado, outro_cliente, with_payer,
):
    if with_payer:
        contrato.interveniente_client_id = outro_cliente.id
        db.commit()
    payer_id = outro_cliente.id if with_payer else contrato.client_id
    item_id = _add_manual_charge(http, rastreador_instalado, contrato.id)
    assert db.query(Billing).count() == 0
    item = db.get(ClientChargeItem, item_id)
    assert item.description == 'Acertado com o cliente'
    assert item.tracker_id == rastreador_instalado.id

    before = http.get('/api/v1/billing-closure/simulate', params={'service_month': '2026-09'})
    assert before.status_code == 200, before.text
    assert before.json()['charge_items'] == []

    params = {'service_month': '2026-10', 'filter_type': 'client', 'client_id': payer_id}
    preview = http.get('/api/v1/billing-closure/simulate', params=params)
    assert preview.status_code == 200, preview.text
    [charge] = preview.json()['charge_items']
    assert charge['item_id'] == item_id
    assert charge['payer_client_id'] == payer_id
    assert charge['vehicle_plate'] == 'ABC1D23'
    assert charge['amount_to_generate'] == 120.50
    assert preview.json()['grand_total'] == 220.40

    result = http.post('/api/v1/billing-closure/generate', params={
        **params, 'contract_ids': contrato.id, 'charge_item_ids': item_id,
    })
    assert result.status_code == 200, result.text
    [billing_id] = result.json()['payment_billing_ids']
    billing = db.get(Billing, billing_id)
    assert billing.billing_type == 'boleto_unico'
    assert billing.amount == Decimal('220.40')
    assert billing.due_date == date(2026, 11, 15)
    assert billing.payer_client_id == payer_id
    components = http.get(f'/api/v1/billings/{billing_id}/components')
    assert components.status_code == 200, components.text
    [service] = [row for row in components.json() if row['item_id'] == item_id]
    assert service['amount'] == 120.50
    assert service['tracker_identifier'] == rastreador_instalado.imei
    assert db.get(Billing, service['id']).tracker_id == rastreador_instalado.id

    again = http.get('/api/v1/billing-closure/simulate', params=params)
    assert again.status_code == 200, again.text
    assert again.json()['charge_items'] == []
    count = db.query(Billing).count()
    repeated = http.post('/api/v1/billing-closure/generate', params=params)
    assert repeated.status_code == 200, repeated.text
    assert db.query(Billing).count() == count


def test_manual_charge_without_contract_is_available_in_closure(http, db, rastreador_instalado):
    item_id = _add_manual_charge(http, rastreador_instalado)
    assert db.query(Billing).count() == 0
    params = {'service_month': '2026-10'}
    preview = http.get('/api/v1/billing-closure/simulate', params=params)
    assert preview.status_code == 200, preview.text
    assert [item['item_id'] for item in preview.json()['charge_items']] == [item_id]
    result = http.post('/api/v1/billing-closure/generate', params={**params, 'charge_item_ids': item_id})
    assert result.status_code == 200, result.text
    [billing_id] = result.json()['payment_billing_ids']
    billing = db.get(Billing, billing_id)
    assert billing.item_id == item_id
    assert billing.amount == Decimal('120.50')
    assert billing.vehicle_id == rastreador_instalado.vehicle_id
    assert billing.tracker_id == rastreador_instalado.id


def test_installation_charge_joins_first_month_without_duplicate(
    http, db, contrato, rastreador_instalado,
):
    item_id = _add_manual_charge(http, rastreador_instalado, contrato.id, start_date='2024-01-15')
    params = {'service_month': '2024-01'}
    preview = http.get('/api/v1/billing-closure/simulate', params=params)
    assert preview.status_code == 200, preview.text
    [monthly] = preview.json()['items']
    assert [charge['item_id'] for charge in monthly['first_month_charges']] == [item_id]
    assert preview.json()['charge_items'] == []
    result = http.post('/api/v1/billing-closure/generate', params={**params, 'contract_ids': contrato.id})
    assert result.status_code == 200, result.text
    [billing_id] = result.json()['payment_billing_ids']
    assert db.get(Billing, billing_id).amount == Decimal(str(preview.json()['grand_total']))
    again = http.get('/api/v1/billing-closure/simulate', params={'service_month': '2024-02'})
    assert again.status_code == 200, again.text
    assert again.json()['charge_items'] == []
