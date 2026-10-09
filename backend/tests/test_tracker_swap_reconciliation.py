from datetime import date
from decimal import Decimal
import json

import pytest
from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.models.billing import Billing
from app.models.contract import Contract
from app.models.enums import BillingStatus, TrackerStatus
from app.models.tracker import Tracker
from app.models.tracker_history import TrackerHistory
from app.models.vehicle import Vehicle


@pytest.fixture()
def stale_contract(db, outro_cliente, plan, rastreador_instalado):
    other_vehicle = Vehicle(client_id=outro_cliente.id, plate='TPV6I75', status='ativo')
    db.add(other_vehicle)
    db.flush()
    contract = Contract(client_id=outro_cliente.id, plan_id=plan.id, vehicle_id=other_vehicle.id,
                        tracker_id=rastreador_instalado.id, start_date=date(2026, 8, 4), status='ativo',
                        billing_day=15, signed=True, signed_at=date(2026, 8, 5))
    db.add(contract)
    db.commit()
    return contract


def swap(http, old, new, **extra):
    return http.post(f'/api/v1/trackers/{old.id}/swap', json={
        'new_tracker_id': new.id, 'reason': 'posição', **extra,
    })


def test_conflict_lists_exact_contract_and_plate_without_changing_any_link(
    http, db, rastreador_instalado, rastreador, veiculo, contrato, stale_contract,
):
    response = swap(http, rastreador_instalado, rastreador, expected_vehicle_id=veiculo.id)
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['code'] == 'inconsistent_active_contract_assignment'
    assert detail['stale_contracts'] == [{'id': stale_contract.id, 'vehicle_id': stale_contract.vehicle_id, 'vehicle_plate': 'TPV6I75'}]
    assert contrato.tracker_id == stale_contract.tracker_id == rastreador_instalado.id
    assert rastreador_instalado.vehicle_id == veiculo.id and rastreador.vehicle_id is None
    assert db.scalar(select(func.count()).select_from(TrackerHistory)) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_reviewed_reference_is_released_atomically_without_canceling_contract_or_billing(
    http, db, rastreador_instalado, rastreador, veiculo, contrato, stale_contract, monkeypatch,
):
    from app.services.multiportal import multiportal_service
    monkeypatch.setattr(multiportal_service, 'query_equipment_link', lambda *a, **kw: pytest.fail('Consulta externa na troca'))
    bills = [Billing(contract_id=c.id, client_id=c.client_id, payer_client_id=c.client_id,
                     amount=Decimal('64.99'), due_date=date(2026, 10, 10), status=BillingStatus.PAID)
             for c in (contrato, stale_contract)]
    db.add_all(bills)
    db.commit()
    original = {c.id: (c.client_id, c.vehicle_id, c.status, c.plan_id, c.start_date, c.billing_day, c.signed, c.signed_at)
                for c in (contrato, stale_contract)}
    old_client = rastreador_instalado.client_id
    previews = {}
    for c in (contrato, stale_contract):
        preview = http.get('/api/v1/billing-closure/simulate', params={
            'service_month': '2026-11', 'filter_type': 'client', 'client_id': c.client_id,
        })
        assert preview.status_code == 200, preview.text
        previews[c.id] = [(i['vehicle_plate'], i['payer_client_id'], i['billing_amount']) for i in preview.json()['items']]
    response = swap(http, rastreador_instalado, rastreador, expected_vehicle_id=veiculo.id,
                    release_stale_contract_ids=[stale_contract.id])
    assert response.status_code == 200, response.text
    assert response.json()['contracts_updated'] == [contrato.id]
    assert response.json()['contracts_reconciled'] == [stale_contract.id]
    for c in (contrato, stale_contract):
        db.refresh(c)
        assert (c.client_id, c.vehicle_id, c.status, c.plan_id, c.start_date, c.billing_day, c.signed, c.signed_at) == original[c.id]
    assert contrato.tracker_id == rastreador.id and stale_contract.tracker_id is None
    db.refresh(rastreador_instalado)
    db.refresh(rastreador)
    assert rastreador_instalado.status == TrackerStatus.STOCK and rastreador_instalado.vehicle_id is None
    assert rastreador.status == TrackerStatus.INSTALLED and rastreador.vehicle_id == veiculo.id
    history = db.scalar(select(TrackerHistory).where(TrackerHistory.action == 'swapped_out'))
    assert history.previous_client_id == old_client
    assert str(stale_contract.id) in history.notes
    audit = db.scalar(select(AuditLog).where(AuditLog.entity_type == 'contract'))
    assert json.loads(audit.description)['previous_tracker_id'] == rastreador_instalado.id
    assert audit.entity_id == stale_contract.id
    for c in (contrato, stale_contract):
        preview = http.get('/api/v1/billing-closure/simulate', params={
            'service_month': '2026-11', 'filter_type': 'client', 'client_id': c.client_id,
        })
        assert preview.status_code == 200, preview.text
        assert [(i['vehicle_plate'], i['payer_client_id'], i['billing_amount']) for i in preview.json()['items']] == previews[c.id]
    assert db.scalar(select(func.count()).select_from(Billing)) == 2
    for bill in bills:
        db.refresh(bill)
        assert bill.status == BillingStatus.PAID and bill.amount == Decimal('64.99')
        assert bill.client_id == bill.payer_client_id
    assert swap(http, rastreador_instalado, rastreador).status_code == 409
    assert db.scalar(select(func.count()).select_from(TrackerHistory)) == 2


@pytest.mark.parametrize('selection', ['wrong', 'duplicate', 'current'])
def test_reconciliation_rejects_stale_or_wrong_selection(
    http, db, rastreador_instalado, rastreador, veiculo, contrato, stale_contract, selection,
):
    ids = {'wrong': [99999], 'duplicate': [stale_contract.id, stale_contract.id], 'current': [contrato.id]}[selection]
    response = swap(http, rastreador_instalado, rastreador, release_stale_contract_ids=ids)
    assert response.status_code == 409
    assert contrato.tracker_id == stale_contract.tracker_id == rastreador_instalado.id
    assert rastreador_instalado.vehicle_id == veiculo.id and rastreador.vehicle_id is None


def test_stock_equipment_with_active_contract_cannot_be_reused(
    http, db, rastreador_instalado, rastreador, contrato, stale_contract,
):
    stale_contract.tracker_id = rastreador.id
    db.commit()
    response = swap(http, rastreador_instalado, rastreador)
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'replacement_has_active_contract'
    assert stale_contract.tracker_id == rastreador.id and contrato.tracker_id == rastreador_instalado.id
    assert http.get('/api/v1/trackers', params={'available_for_swap': True}).json()['items'] == []


@pytest.mark.parametrize('inactive', ['canceled', 'deleted'])
def test_stock_with_only_historical_contract_is_available(http, db, rastreador, stale_contract, inactive):
    stale_contract.tracker_id = rastreador.id
    if inactive == 'canceled':
        stale_contract.status = 'cancelado'
    else:
        stale_contract.is_deleted = True
    db.commit()
    result = http.get('/api/v1/trackers', params={'available_for_swap': True}).json()
    assert [row['id'] for row in result['items']] == [rastreador.id]


def test_wrong_vehicle_does_not_swap_or_release_reference(http, db, rastreador_instalado, rastreador, contrato, stale_contract):
    response = swap(http, rastreador_instalado, rastreador, expected_vehicle_id=stale_contract.vehicle_id,
                    release_stale_contract_ids=[stale_contract.id])
    assert response.status_code == 409
    assert contrato.tracker_id == stale_contract.tracker_id == rastreador_instalado.id


def test_commit_failure_rolls_back_swap_and_reference_release(
    http, db, rastreador_instalado, rastreador, veiculo, contrato, stale_contract, monkeypatch,
):
    monkeypatch.setattr(db, 'commit', lambda: (_ for _ in ()).throw(RuntimeError('Falha de gravação')))
    response = swap(http, rastreador_instalado, rastreador, release_stale_contract_ids=[stale_contract.id])
    assert response.status_code == 500
    for row in (rastreador_instalado, rastreador, contrato, stale_contract):
        db.refresh(row)
    assert contrato.tracker_id == stale_contract.tracker_id == rastreador_instalado.id
    assert rastreador_instalado.vehicle_id == veiculo.id and rastreador.vehicle_id is None
    assert db.scalar(select(func.count()).select_from(TrackerHistory)) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_financial_role_cannot_release_contract_reference(http_fin, rastreador_instalado, rastreador, contrato, stale_contract):
    response = swap(http_fin, rastreador_instalado, rastreador, release_stale_contract_ids=[stale_contract.id])
    assert response.status_code == 403
    assert contrato.tracker_id == stale_contract.tracker_id == rastreador_instalado.id
