from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.billing import Billing
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus, TrackerStatus
from app.models.tracker import Tracker
from app.models.tracker_history import TrackerHistory
from app.models.vehicle import Vehicle


def _url(vehicle):
    return f'/api/v1/vehicles/{vehicle.id}/change-client'


def test_sale_moves_only_selected_vehicle_and_next_closure(
    http, db, cliente, outro_cliente, veiculo, contrato, rastreador_instalado, plan, monkeypatch,
):
    from app.core.config import settings
    from app.services.multiportal import multiportal_service

    monkeypatch.setattr(settings, 'multiportal_enabled', True)
    # A falha da plataforma não pode participar da troca local.
    monkeypatch.setattr(multiportal_service, 'sync_vehicle', lambda *args: pytest.fail('Chamada externa na troca local'))
    monkeypatch.setattr(multiportal_service, 'query_equipment_link', lambda *args, **kwargs: pytest.fail('Consulta externa na troca local'))
    monkeypatch.setattr(multiportal_service, 'unlink_vehicle_client', lambda *args: pytest.fail('Desvínculo externo na troca local'))
    other_vehicle = Vehicle(client_id=cliente.id, plate='ZZZ1A23', status='ativo')
    db.add(other_vehicle)
    db.flush()
    other_tracker = Tracker(imei='111111111111111', vehicle_id=other_vehicle.id, client_id=cliente.id, status=TrackerStatus.INSTALLED)
    db.add(other_tracker)
    db.flush()
    other_contract = Contract(client_id=cliente.id, vehicle_id=other_vehicle.id, tracker_id=other_tracker.id, plan_id=plan.id, start_date=date(2024, 1, 15), status='ativo', billing_day=15)
    db.add(other_contract)
    db.commit()
    original = (contrato.id, contrato.start_date, contrato.plan_id, contrato.billing_day, rastreador_instalado.install_date)

    response = http.post(_url(veiculo), json={'client_id': outro_cliente.id, 'interveniente_client_id': None, 'expected_client_id': cliente.id})
    assert response.status_code == 200, response.text
    assert response.json()['client_id'] == outro_cliente.id
    db.refresh(contrato)
    db.refresh(rastreador_instalado)
    assert contrato.client_id == rastreador_instalado.client_id == outro_cliente.id
    assert (contrato.id, contrato.start_date, contrato.plan_id, contrato.billing_day, rastreador_instalado.install_date) == original
    assert rastreador_instalado.vehicle_id == veiculo.id
    assert rastreador_instalado.status == TrackerStatus.INSTALLED
    assert other_vehicle.client_id == other_contract.client_id == other_tracker.client_id == cliente.id

    for client_id, expected_plate in ((cliente.id, other_vehicle.plate), (outro_cliente.id, veiculo.plate)):
        preview = http.get('/api/v1/billing-closure/simulate', params={'service_month': '2026-10', 'filter_type': 'client', 'client_id': client_id})
        assert preview.status_code == 200, preview.text
        assert [item['vehicle_plate'] for item in preview.json()['items']] == [expected_plate]
        assert preview.json()['items'][0]['payer_client_id'] == client_id
    # A edição normal do equipamento também volta a funcionar.
    saved = http.put(f'/api/v1/trackers/{rastreador_instalado.id}', json={'vehicle_id': veiculo.id, 'client_id': outro_cliente.id, 'model': 'Novo modelo'})
    assert saved.status_code == 200, saved.text
    history = db.scalar(select(TrackerHistory).where(TrackerHistory.action == 'client_changed'))
    assert (history.previous_client_id, history.new_client_id) == (cliente.id, outro_cliente.id)
    assert db.scalar(select(AuditLog).where(AuditLog.entity_type == 'vehicle')).entity_id == veiculo.id


@pytest.mark.parametrize('status', [BillingStatus.PENDING, BillingStatus.PAID, BillingStatus.CANCELED])
def test_preserves_existing_billing_and_legacy_payer(http, db, veiculo, contrato, cliente, outro_cliente, status):
    billing = Billing(contract_id=contrato.id, client_id=cliente.id, payer_client_id=None, amount=Decimal('64.99'), due_date=date(2026, 10, 20), status=status, billing_type='recorrente', period_label='10/2026')
    db.add(billing)
    db.commit()
    response = http.post(_url(veiculo), json={'client_id': outro_cliente.id, 'interveniente_client_id': None})
    assert response.status_code == 200, response.text
    db.refresh(billing)
    assert billing.client_id == cliente.id
    assert billing.payer_client_id == cliente.id
    assert billing.status == status
    assert billing.amount == Decimal('64.99')
    assert billing.due_date == date(2026, 10, 20)


def test_payer_only_preserves_owner_and_old_intervenient_snapshot(http_fin, db, veiculo, contrato, cliente, outro_cliente, rastreador_instalado):
    contrato.interveniente_client_id = outro_cliente.id
    legacy = Billing(contract_id=contrato.id, client_id=cliente.id, amount=Decimal('64.99'), due_date=date(2026, 10, 20), status=BillingStatus.PAID)
    db.add(legacy)
    db.commit()
    response = http_fin.post(_url(veiculo), json={'client_id': cliente.id, 'interveniente_client_id': None})
    assert response.status_code == 200, response.text
    db.refresh(contrato)
    db.refresh(legacy)
    assert contrato.client_id == rastreador_instalado.client_id == cliente.id
    assert contrato.interveniente_client_id is None
    assert legacy.payer_client_id == outro_cliente.id


def test_new_intervenient_receives_next_closure(http, db, veiculo, contrato, cliente, outro_cliente):
    response = http.post(_url(veiculo), json={'client_id': cliente.id, 'interveniente_client_id': outro_cliente.id})
    assert response.status_code == 200, response.text
    db.refresh(contrato)
    assert contrato.client_id == cliente.id
    assert contrato.interveniente_client_id == outro_cliente.id
    preview = http.get('/api/v1/billing-closure/simulate', params={'service_month': '2026-10', 'filter_type': 'client', 'client_id': outro_cliente.id})
    assert preview.status_code == 200, preview.text
    assert preview.json()['items'][0]['payer_client_id'] == outro_cliente.id


def test_owner_edit_uses_atomic_flow(http, db, veiculo, contrato, outro_cliente, rastreador_instalado):
    response = http.put(f'/api/v1/vehicles/{veiculo.id}', json={'client_id': outro_cliente.id, 'model': 'Discovery'})
    assert response.status_code == 200, response.text
    db.refresh(contrato)
    db.refresh(rastreador_instalado)
    assert contrato.client_id == rastreador_instalado.client_id == outro_cliente.id


def test_repairs_already_changed_vehicle_from_video(http, db, veiculo, contrato, outro_cliente, rastreador_instalado):
    veiculo.client_id = outro_cliente.id
    db.commit()
    response = http.post(_url(veiculo), json={'client_id': outro_cliente.id, 'interveniente_client_id': None})
    assert response.status_code == 200, response.text
    db.refresh(contrato)
    db.refresh(rastreador_instalado)
    assert contrato.client_id == rastreador_instalado.client_id == outro_cliente.id


def test_active_service_follows_contract_and_old_installment_keeps_owner(http, db, veiculo, contrato, cliente, outro_cliente):
    item = ClientChargeItem(client_id=cliente.id, contract_id=contrato.id, vehicle_id=veiculo.id, title='Serviço', quantity=1, unit_price=Decimal('20'), total_amount=Decimal('40'), installment_count=2, start_date=date(2026, 10, 1), active=True)
    db.add(item)
    db.flush()
    old = Billing(contract_id=contrato.id, client_id=cliente.id, item_id=item.id, amount=Decimal('20'), due_date=date(2026, 10, 20), status=BillingStatus.PAID, installment_number=1, installment_total=2, billing_type='item')
    db.add(old)
    db.commit()
    response = http.post(_url(veiculo), json={'client_id': outro_cliente.id})
    assert response.status_code == 200, response.text
    db.refresh(item)
    db.refresh(old)
    assert item.client_id == outro_cliente.id
    assert old.client_id == old.payer_client_id == cliente.id
    preview = http.get('/api/v1/billing-closure/simulate', params={'service_month': '2026-10', 'filter_type': 'client', 'client_id': outro_cliente.id})
    assert preview.status_code == 200, preview.text


def test_canceled_contract_and_explicit_snapshot_unchanged(http, db, veiculo, contrato, cliente, outro_cliente, plan):
    canceled = Contract(client_id=cliente.id, vehicle_id=veiculo.id, plan_id=plan.id, start_date=date(2023, 1, 1), status='cancelado')
    billing = Billing(contract_id=contrato.id, client_id=cliente.id, payer_client_id=outro_cliente.id, amount=Decimal('20'), due_date=date(2026, 10, 20), status=BillingStatus.PAID)
    db.add_all([canceled, billing])
    db.commit()
    response = http.post(_url(veiculo), json={'client_id': outro_cliente.id})
    assert response.status_code == 200, response.text
    db.refresh(canceled)
    db.refresh(billing)
    assert canceled.client_id == cliente.id
    assert billing.payer_client_id == outro_cliente.id


@pytest.mark.parametrize('payload', [{'client_id': 999999}, {'client_id': 1, 'interveniente_client_id': 999999}])
def test_unknown_reference_rejected_without_partial_changes(http, db, veiculo, contrato, cliente, rastreador_instalado, payload):
    response = http.post(_url(veiculo), json=payload)
    assert response.status_code == 404, response.text
    db.refresh(veiculo)
    db.refresh(contrato)
    db.refresh(rastreador_instalado)
    assert veiculo.client_id == contrato.client_id == rastreador_instalado.client_id == cliente.id


def test_stale_screen_rejected(http, db, veiculo, contrato, cliente, outro_cliente):
    response = http.post(_url(veiculo), json={'client_id': outro_cliente.id, 'expected_client_id': outro_cliente.id})
    assert response.status_code == 409
    db.refresh(contrato)
    assert contrato.client_id == cliente.id


def test_inconsistent_tracker_rejected_without_changing_vehicle(http, db, veiculo, veiculo_outro_cliente, contrato, rastreador_instalado, cliente, outro_cliente):
    rastreador_instalado.vehicle_id = veiculo_outro_cliente.id
    db.commit()
    response = http.post(_url(veiculo), json={'client_id': outro_cliente.id})
    assert response.status_code == 409, response.text
    db.refresh(veiculo)
    db.refresh(contrato)
    assert veiculo.client_id == contrato.client_id == cliente.id


def test_operational_can_transfer(http_op, veiculo, outro_cliente):
    assert http_op.post(_url(veiculo), json={'client_id': outro_cliente.id}).status_code == 200


def test_financial_cannot_change_vehicle_owner(http_fin, veiculo, outro_cliente):
    assert http_fin.post(_url(veiculo), json={'client_id': outro_cliente.id}).status_code == 403


def test_client_role_denied(http_cliente, veiculo, outro_cliente):
    assert http_cliente.post(_url(veiculo), json={'client_id': outro_cliente.id}).status_code == 403


def test_deleted_client_denied(http, db, veiculo, outro_cliente):
    outro_cliente.is_deleted = True
    db.commit()
    assert http.post(_url(veiculo), json={'client_id': outro_cliente.id}).status_code == 404


def test_deleted_vehicle_denied(http, db, veiculo, outro_cliente):
    veiculo.is_deleted = True
    db.commit()
    assert http.post(_url(veiculo), json={'client_id': outro_cliente.id}).status_code == 404


def test_all_installed_trackers_and_active_contracts_follow_same_plate(http, db, veiculo, contrato, cliente, outro_cliente, plan):
    second_tracker = Tracker(imei='222222222222222', client_id=cliente.id, vehicle_id=veiculo.id, status=TrackerStatus.INSTALLED)
    db.add(second_tracker)
    db.flush()
    second_contract = Contract(client_id=cliente.id, vehicle_id=veiculo.id, tracker_id=second_tracker.id, plan_id=plan.id, status='ativo', start_date=date(2025, 1, 1))
    db.add(second_contract)
    db.commit()
    response = http.post(_url(veiculo), json={'client_id': outro_cliente.id, 'interveniente_client_id': None})
    assert response.status_code == 200, response.text
    for record in (contrato, second_contract, second_tracker):
        db.refresh(record)
        assert record.client_id == outro_cliente.id


def test_paid_billing_api_keeps_legacy_payer_after_change(http, db, veiculo, contrato, cliente, outro_cliente, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, 'integration_api_key', 'test-local-api-key')
    contrato.interveniente_client_id = outro_cliente.id
    billing = Billing(contract_id=contrato.id, client_id=cliente.id, amount=Decimal('64.99'), due_date=date(2026, 10, 20), status=BillingStatus.PAID)
    db.add(billing)
    db.commit()
    response = http.post(_url(veiculo), json={'client_id': cliente.id, 'interveniente_client_id': None})
    assert response.status_code == 200, response.text
    historical = http.get(f'/api/v1/integrations/cobrancas/{billing.id}', headers={'X-API-Key': 'test-local-api-key'})
    assert historical.status_code == 200, historical.text
    assert historical.json()['cliente']['id'] == outro_cliente.id


def test_legacy_deleted_intervenient_keeps_original_client_fallback(http, db, veiculo, contrato, cliente, outro_cliente):
    removed_payer = Client(name='Pagador removido', cpf_cnpj='12312312312', type='pf', is_deleted=True)
    db.add(removed_payer)
    db.flush()
    contrato.interveniente_client_id = removed_payer.id
    billing = Billing(contract_id=contrato.id, client_id=cliente.id, amount=Decimal('64.99'), due_date=date(2026, 10, 20), status=BillingStatus.PAID)
    db.add(billing)
    db.commit()
    response = http.post(_url(veiculo), json={'client_id': outro_cliente.id, 'interveniente_client_id': None})
    assert response.status_code == 200, response.text
    db.refresh(billing)
    assert billing.payer_client_id == cliente.id


@pytest.mark.parametrize('search', ['abc1d23', 'ABC-1D23', 'ABC 1D23', 'C1D'])
def test_tracker_search_by_plate(http, veiculo, rastreador_instalado, search):
    response = http.get('/api/v1/trackers', params={'search': search, 'limit': 1})
    assert response.status_code == 200, response.text
    assert response.json()['total'] == 1
    assert response.json()['items'][0]['id'] == rastreador_instalado.id


def test_tracker_search_excludes_deleted_vehicle_plate(http, db, veiculo, rastreador_instalado):
    veiculo.is_deleted = True
    db.commit()
    response = http.get('/api/v1/trackers', params={'search': veiculo.plate})
    assert response.status_code == 200
    assert response.json()['total'] == 0
