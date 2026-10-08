from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.billing import Billing
from app.models.contract import Contract
from app.models.enums import BillingStatus, TrackerStatus
from app.models.multiportal_outbox import MultiportalOutbox
from app.models.tracker import Tracker
from app.models.tracker_history import TrackerHistory


def _body(vehicle, client, **updates):
    return {
        'client_id': client.id,
        'expected_vehicle_id': vehicle.id,
        'expected_client_id': vehicle.client_id,
        'tracker_update': updates,
    }


def _url(tracker):
    return f'/api/v1/trackers/{tracker.id}/change-client'


def test_editor_changes_owner_and_technical_data_in_one_operation(
    http, db, veiculo, cliente, outro_cliente, contrato, rastreador_instalado, plan, monkeypatch,
):
    from app.core.config import settings
    from app.services.multiportal import multiportal_service

    monkeypatch.setattr(settings, 'multiportal_enabled', True)
    for method in ('query_equipment_link', 'unlink_vehicle_client', 'sync_vehicle'):
        monkeypatch.setattr(multiportal_service, method, lambda *a, **kw: pytest.fail('Chamada externa durante a troca'))
    rastreador_instalado.imei = rastreador_instalado.serial_number = 'DES000005'
    contrato.interveniente_client_id = outro_cliente.id
    billing = Billing(
        contract_id=contrato.id, client_id=cliente.id, payer_client_id=None,
        amount=Decimal('64.99'), due_date=date(2026, 10, 10), status=BillingStatus.PAID,
    )
    sibling = Tracker(imei='555555555555555', client_id=cliente.id, vehicle_id=veiculo.id, status=TrackerStatus.INSTALLED)
    db.add_all([billing, sibling])
    db.flush()
    sibling_contract = Contract(client_id=cliente.id, tracker_id=sibling.id, vehicle_id=veiculo.id, plan_id=plan.id, start_date=date(2025, 1, 1), status='ativo')
    db.add(sibling_contract)
    db.commit()
    original = (contrato.id, contrato.plan_id, contrato.start_date, contrato.billing_day, rastreador_instalado.install_date)

    response = http.post(_url(rastreador_instalado), json=_body(
        veiculo, outro_cliente, model='ST 300', notes='Cliente atualizado', sim_number='47999652889',
        status='instalado', install_date=rastreador_instalado.install_date.isoformat(),
    ))
    assert response.status_code == 200, response.text
    assert response.json()['imei'] == 'DES000005'
    assert response.json()['vehicle_plate'] == veiculo.plate
    for row in (veiculo, contrato, rastreador_instalado, sibling, sibling_contract):
        db.refresh(row)
        assert row.client_id == outro_cliente.id
    assert rastreador_instalado.imei == rastreador_instalado.serial_number == 'DES000005'
    assert rastreador_instalado.model == 'ST 300'
    assert rastreador_instalado.notes == 'Cliente atualizado'
    assert rastreador_instalado.sim_number == '47999652889'
    assert (contrato.id, contrato.plan_id, contrato.start_date, contrato.billing_day, rastreador_instalado.install_date) == original
    assert contrato.interveniente_client_id is None
    assert rastreador_instalado.vehicle_id == veiculo.id
    assert rastreador_instalado.status == TrackerStatus.INSTALLED
    db.refresh(billing)
    assert billing.client_id == cliente.id
    assert billing.payer_client_id == outro_cliente.id  # Pagador anterior preservado.
    assert billing.status == BillingStatus.PAID
    assert len(db.scalars(select(TrackerHistory).where(TrackerHistory.action == 'client_changed')).all()) == 2
    assert len(db.scalars(select(MultiportalOutbox)).all()) == 2  # Uma intenção por equipamento.


@pytest.mark.parametrize('updates,code', [
    ({'imei': '123456789012345'}, 409),  # ID do rastreador em estoque.
    ({'imei': 'abc'}, 422),
    ({'client_id': 999}, 422),
    ({'vehicle_id': None}, 422),
    ({'status': 'em_estoque'}, 409),
    ({'install_date': '2026-10-08'}, 409),
])
def test_invalid_save_does_not_partially_transfer_or_freeze_billings(
    http, db, veiculo, cliente, outro_cliente, contrato, rastreador_instalado, rastreador, updates, code,
):
    billing = Billing(contract_id=contrato.id, client_id=cliente.id, amount=Decimal('64.99'), due_date=date(2026, 10, 10), status=BillingStatus.PENDING)
    db.add(billing)
    db.commit()
    response = http.post(_url(rastreador_instalado), json=_body(veiculo, outro_cliente, **updates))
    assert response.status_code == code, response.text
    for row in (veiculo, contrato, rastreador_instalado):
        db.refresh(row)
        assert row.client_id == cliente.id
    db.refresh(billing)
    assert billing.payer_client_id is None
    assert db.scalar(select(TrackerHistory).where(TrackerHistory.action == 'client_changed')) is None
    assert db.scalar(select(MultiportalOutbox)) is None


def test_rejects_screen_with_old_vehicle_owner(http, db, veiculo, cliente, outro_cliente, rastreador_instalado):
    payload = _body(veiculo, outro_cliente)
    veiculo.client_id = outro_cliente.id
    db.commit()
    response = http.post(_url(rastreador_instalado), json=payload)
    assert response.status_code == 409
    db.refresh(rastreador_instalado)
    assert rastreador_instalado.client_id == cliente.id


def test_rejects_tracker_no_longer_installed_on_expected_plate(http, db, veiculo, veiculo_outro_cliente, cliente, outro_cliente, rastreador_instalado):
    payload = _body(veiculo, outro_cliente)
    rastreador_instalado.vehicle_id = veiculo_outro_cliente.id
    db.commit()
    response = http.post(_url(rastreador_instalado), json=payload)
    assert response.status_code == 409, response.text
    db.refresh(veiculo)
    assert veiculo.client_id == cliente.id
    assert db.scalar(select(TrackerHistory)) is None


@pytest.mark.parametrize('http_fixture,code', [('http_op', 200), ('http_fin', 403), ('http_cliente', 403), ('http_unauth', 401)])
def test_permissions(request, http_fixture, code, veiculo, outro_cliente, rastreador_instalado):
    response = request.getfixturevalue(http_fixture).post(_url(rastreador_instalado), json=_body(veiculo, outro_cliente))
    assert response.status_code == code, response.text


def test_missing_client_does_not_change_owner(http, db, veiculo, cliente, outro_cliente, rastreador_instalado):
    payload = _body(veiculo, outro_cliente)
    payload['client_id'] = 999999
    response = http.post(_url(rastreador_instalado), json=payload)
    assert response.status_code == 404
    db.refresh(veiculo)
    assert veiculo.client_id == cliente.id


def test_can_repair_existing_local_inconsistency_without_resetting_payer(http, db, veiculo, outro_cliente, contrato, rastreador_instalado):
    veiculo.client_id = outro_cliente.id
    contrato.interveniente_client_id = outro_cliente.id
    db.commit()
    response = http.post(_url(rastreador_instalado), json=_body(veiculo, outro_cliente, notes='Corrigido'))
    assert response.status_code == 200, response.text
    db.refresh(contrato)
    assert contrato.client_id == outro_cliente.id
    assert contrato.interveniente_client_id == outro_cliente.id


def test_generic_editor_still_rejects_vehicle_removal(http, veiculo, rastreador_instalado):
    response = http.put(f'/api/v1/trackers/{rastreador_instalado.id}', json={'vehicle_id': None})
    assert response.status_code == 409
