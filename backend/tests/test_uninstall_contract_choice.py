import json
from datetime import date
from decimal import Decimal

import pytest

from app.models.audit_log import AuditLog
from app.models.billing import Billing
from app.models.enums import BillingStatus, TrackerStatus, VehicleStatus
from app.models.tracker_history import TrackerHistory
from app.models.uninstall_event import UninstallEvent
from app.models.vehicle import Vehicle
from app.services.billing_closure import simulate_closure


def uninstall(http, vehicle, **flags):
    return http.post(f'/api/v1/vehicles/{vehicle.id}/uninstall', params={
        'uninstall_date': '2025-05-15', 'uninstall_fee': 160, **flags,
    })


def test_default_preserves_financial_contract_and_existing_billing(
    http, db, veiculo, rastreador_instalado, contrato,
):
    billing = Billing(contract_id=contrato.id, client_id=contrato.client_id,
        amount=Decimal('99.90'), due_date=date(2025, 6, 15), status=BillingStatus.PENDING,
        billing_type='recorrente', period_label='06/2025', notes='Mensalidade integral')
    db.add(billing)
    db.commit()
    financial_before = {column.name: getattr(contrato, column.name)
        for column in contrato.__table__.columns if column.name not in ('tracker_id', 'updated_at')}
    billing_before = {column.name: getattr(billing, column.name) for column in billing.__table__.columns}

    response = uninstall(http, veiculo)
    assert response.status_code == 200, response.text
    assert response.json()['contracts_closed'] == 0
    assert response.json()['contracts_kept_active'] == 1
    assert response.json()['source_prorated_amount'] is None
    assert response.json()['ajustes_pro_rata_pendentes'] == []
    for row in (contrato, billing, rastreador_instalado, veiculo):
        db.refresh(row)
    assert financial_before == {column.name: getattr(contrato, column.name)
        for column in contrato.__table__.columns if column.name not in ('tracker_id', 'updated_at')}
    assert billing_before == {column.name: getattr(billing, column.name) for column in billing.__table__.columns}
    assert contrato.tracker_id is None
    assert rastreador_instalado.status == TrackerStatus.STOCK
    assert veiculo.status == VehicleStatus.REMOVED
    event = db.query(UninstallEvent).one()
    assert event.fee_amount == Decimal('160.00') and event.contract_id == contrato.id
    audit = json.loads(db.query(AuditLog).filter_by(path=f'/vehicles/{veiculo.id}/uninstall').one().description)
    assert audit['encerrar_contratos'] is False
    assert audit['confirmar_encerramento_contratos'] is False
    assert audit['contracts_kept_active'] == [contrato.id]
    assert audit['contract_tracker_references_released'] == [contrato.id]

    closure = simulate_closure(db, date(2025, 7, 1))
    item = next(item for item in closure['items'] if item.get('contract_id') == contrato.id)
    assert item['billing_amount'] == 99.9
    assert not item.get('is_final_prorata')
    assert item['tracker_imei'] is None


def test_closing_without_second_confirmation_changes_nothing(
    http, db, veiculo, rastreador_instalado, contrato,
):
    response = uninstall(http, veiculo, encerrar_contratos=True)
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'contract_closure_confirmation_required'
    for row in (contrato, rastreador_instalado, veiculo):
        db.refresh(row)
    assert contrato.status == 'ativo' and contrato.tracker_id == rastreador_instalado.id
    assert veiculo.status != VehicleStatus.REMOVED
    assert rastreador_instalado.vehicle_id == veiculo.id
    assert db.query(UninstallEvent).count() == 0
    assert db.query(TrackerHistory).count() == 0
    assert db.query(AuditLog).count() == 0


def test_explicit_closing_requires_both_flags_and_stops_future_monthly_closures(
    http, db, veiculo, contrato,
):
    response = uninstall(http, veiculo, encerrar_contratos=True, confirmar_encerramento_contratos=True)
    assert response.status_code == 200, response.text
    assert response.json()['contracts_closed'] == 1
    assert response.json()['contracts_kept_active'] == 0
    db.refresh(contrato)
    assert contrato.status == 'cancelado'
    assert contrato.end_date == contrato.uninstalled_at == date(2025, 5, 15)
    audit = json.loads(db.query(AuditLog).filter_by(path=f'/vehicles/{veiculo.id}/uninstall').one().description)
    assert audit['encerrar_contratos'] and audit['confirmar_encerramento_contratos']
    assert audit['contracts_closed'] == [contrato.id]
    closure = simulate_closure(db, date(2025, 7, 1))
    assert not any(item.get('contract_id') == contrato.id for item in closure['items'])


def test_stock_tracker_can_be_reused_without_closing_the_previous_contract(
    http, db, veiculo, rastreador_instalado, contrato, outro_cliente,
):
    destination = Vehicle(client_id=outro_cliente.id, plate='BCD1234', type='passeio')
    db.add(destination)
    db.commit()
    assert uninstall(http, veiculo).status_code == 200
    response = http.post(f'/api/v1/trackers/{rastreador_instalado.id}/link-vehicle', json={
        'vehicle_id': destination.id, 'start_date': '2025-05-16',
    })
    assert response.status_code == 200, response.text
    db.refresh(contrato)
    db.refresh(rastreador_instalado)
    assert contrato.status == 'ativo' and contrato.vehicle_id == veiculo.id
    assert contrato.client_id == veiculo.client_id and contrato.tracker_id is None
    assert rastreador_instalado.vehicle_id == destination.id
    assert rastreador_instalado.client_id == outro_cliente.id


@pytest.mark.parametrize('confirm', [False, True])
def test_secondary_confirmation_alone_cannot_close_contracts(http, db, veiculo, contrato, confirm):
    response = uninstall(http, veiculo, encerrar_contratos=False, confirmar_encerramento_contratos=confirm)
    assert response.status_code == 200, response.text
    db.refresh(contrato)
    assert contrato.status == 'ativo'
    assert contrato.end_date is None and contrato.uninstalled_at is None


@pytest.mark.parametrize('close', [False, True])
def test_failed_commit_rolls_back_the_selected_mode(http, db, veiculo, rastreador_instalado, contrato, monkeypatch, close):
    def fail_commit():
        raise RuntimeError('simulated commit failure')
    monkeypatch.setattr(db, 'commit', fail_commit)
    response = uninstall(http, veiculo, encerrar_contratos=close, confirmar_encerramento_contratos=close)
    assert response.status_code == 500
    for row in (contrato, rastreador_instalado, veiculo):
        db.refresh(row)
    assert contrato.status == 'ativo' and contrato.tracker_id == rastreador_instalado.id
    assert contrato.end_date is None and contrato.uninstalled_at is None
    assert rastreador_instalado.vehicle_id == veiculo.id
    assert veiculo.status != VehicleStatus.REMOVED
    assert db.query(UninstallEvent).count() == 0
    assert db.query(TrackerHistory).count() == 0
    assert db.query(AuditLog).count() == 0
