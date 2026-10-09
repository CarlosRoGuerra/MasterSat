from datetime import date
from io import BytesIO

import pytest
from fastapi import HTTPException
from pypdf import PdfReader
from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.models.billing import Billing
from app.models.contract import Contract
from app.models.document import Document
from app.models.enums import BillingStatus, TrackerStatus
from app.models.tracker_history import TrackerHistory
from scripts.reconciliar_proprietario_veiculo import reconcile


@pytest.mark.parametrize('action', ['save_owner', 'relink_same_vehicle', 'cli'])
def test_incomplete_owner_transfer_reconciles_without_reinstall_or_new_contract(
    http, db, cliente, outro_cliente, veiculo, rastreador_instalado, contrato, monkeypatch, action,
):
    from app.services.multiportal import multiportal_service
    for method in ('query_equipment_link', 'sync_vehicle', 'sync_client', 'unlink_vehicle_client', 'unlink_equipment_vehicle'):
        monkeypatch.setattr(multiportal_service, method, lambda *a, **kw: pytest.fail('Consulta externa na reconciliação'))
    cliente.name = 'CLEOVIR DOS SANTOS BORGES VALENDOLF'
    outro_cliente.name = 'RUBERVAL DA SILVA FILHO'
    veiculo.plate = 'OXD0A94'
    veiculo.client_id = outro_cliente.id
    rastreador_instalado.imei = '869671078139498'
    contrato.signed = True
    contrato.signed_at = date(2024, 1, 16)
    old_document = Document(file_name='contrato-cleovir.pdf', object_key='cliente/contrato-antigo.pdf',
                            content_type='application/pdf', size_bytes=10, reference_type='client',
                            reference_id=cliente.id, category='contrato', active=True)
    billing = Billing(contract_id=contrato.id, client_id=cliente.id, payer_client_id=None,
                      amount=64.99, due_date=date(2026, 10, 10), status=BillingStatus.PAID)
    db.add_all([old_document, billing])
    db.commit()
    original = (rastreador_instalado.vehicle_id, rastreador_instalado.install_date,
                contrato.plan_id, contrato.start_date, contrato.billing_day)

    if action == 'save_owner':
        response = http.put(f'/api/v1/vehicles/{veiculo.id}', json={'client_id': outro_cliente.id})
        assert response.status_code == 200, response.text
    elif action == 'relink_same_vehicle':
        response = http.post(f'/api/v1/trackers/{rastreador_instalado.id}/link-vehicle', json={'vehicle_id': veiculo.id})
        assert response.status_code == 200, response.text
        assert response.json()['tracker']['client_id'] == outro_cliente.id
        assert response.json()['contract'] is None
    else:
        result = reconcile(db, plate=veiculo.plate, imei=rastreador_instalado.imei,
                           expected_client_name=outro_cliente.name, operator='administrator')
        db.commit()
        assert result['contratos_atualizados'] == [contrato.id]
        assert result['rastreadores_atualizados'] == [rastreador_instalado.id]

    for row in (contrato, rastreador_instalado, billing, old_document):
        db.refresh(row)
    assert contrato.client_id == rastreador_instalado.client_id == outro_cliente.id
    assert rastreador_instalado.status == TrackerStatus.INSTALLED
    assert (rastreador_instalado.vehicle_id, rastreador_instalado.install_date,
            contrato.plan_id, contrato.start_date, contrato.billing_day) == original
    assert contrato.status == 'ativo' and not contrato.signed and contrato.signed_at is None
    assert billing.client_id == billing.payer_client_id == cliente.id
    assert billing.status == BillingStatus.PAID and float(billing.amount) == 64.99
    assert old_document.reference_id == cliente.id and old_document.active
    assert db.scalar(select(func.count()).select_from(Contract)) == 1
    assert db.scalar(select(func.count()).select_from(Billing)) == 1
    assert db.scalar(select(func.count()).select_from(TrackerHistory).where(TrackerHistory.action == 'client_changed')) == 1
    audit = db.scalar(select(AuditLog).where(AuditLog.entity_type == 'vehicle'))
    assert '"signed": true' in audit.description
    assert http.get('/api/v1/contracts', params={'client_id': outro_cliente.id}).json()[0]['id'] == contrato.id
    pdf = http.get(f'/api/v1/contracts/{contrato.id}/pdf')
    assert pdf.status_code == 200, pdf.text
    reader = PdfReader(BytesIO(pdf.content))
    fields = reader.get_fields()
    assert fields['cliente_nome']['/V'] == outro_cliente.name
    assert fields['placas']['/V'] == veiculo.plate
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert outro_cliente.name in text
    assert cliente.name not in text


def test_same_vehicle_retry_preserves_signature_and_does_not_repeat_history(http, db, veiculo, contrato, rastreador_instalado):
    contrato.signed = True
    contrato.signed_at = date(2024, 1, 16)
    db.commit()
    for _ in range(2):
        response = http.post(f'/api/v1/trackers/{rastreador_instalado.id}/link-vehicle', json={'vehicle_id': veiculo.id})
        assert response.status_code == 200, response.text
    db.refresh(contrato)
    assert contrato.signed and contrato.signed_at == date(2024, 1, 16)
    assert db.scalar(select(func.count()).select_from(TrackerHistory)) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_changing_payer_only_preserves_current_owner_signature(http, db, cliente, outro_cliente, veiculo, contrato):
    contrato.signed = True
    contrato.signed_at = date(2024, 1, 16)
    db.commit()
    response = http.post(f'/api/v1/vehicles/{veiculo.id}/change-client',
                         json={'client_id': cliente.id, 'interveniente_client_id': outro_cliente.id})
    assert response.status_code == 200, response.text
    db.refresh(contrato)
    assert contrato.signed and contrato.signed_at == date(2024, 1, 16)


def test_reconciliation_simulation_is_fully_reversible(db, outro_cliente, veiculo, contrato, rastreador_instalado):
    old_client = contrato.client_id
    veiculo.client_id = outro_cliente.id
    db.commit()
    reconcile(db, plate=veiculo.plate, imei=rastreador_instalado.imei,
              expected_client_name=outro_cliente.name, operator='administrator')
    db.rollback()
    db.refresh(contrato)
    db.refresh(rastreador_instalado)
    assert contrato.client_id == rastreador_instalado.client_id == old_client
    assert db.scalar(select(func.count()).select_from(TrackerHistory)) == 0


@pytest.mark.parametrize('wrong_field', ['plate', 'imei', 'expected_client_name'])
def test_cli_refuses_mismatched_target(db, veiculo, contrato, cliente, rastreador_instalado, wrong_field):
    args = {'plate': veiculo.plate, 'imei': rastreador_instalado.imei,
            'expected_client_name': cliente.name, 'operator': 'administrator'}
    args[wrong_field] = 'OUTRO'
    with pytest.raises(HTTPException):
        reconcile(db, **args)
    assert db.scalar(select(func.count()).select_from(TrackerHistory)) == 0
    assert contrato.client_id == rastreador_instalado.client_id == cliente.id


def test_vehicle_owner_filter_finds_tracker_with_stale_client_without_mutating(
    http, db, cliente, outro_cliente, veiculo, rastreador_instalado,
):
    veiculo.client_id = outro_cliente.id
    db.commit()
    result = http.get('/api/v1/trackers', params={'vehicle_client_id': outro_cliente.id})
    assert result.status_code == 200, result.text
    assert [row['id'] for row in result.json()['items']] == [rastreador_instalado.id]
    assert result.json()['items'][0]['client_id'] == cliente.id
    assert http.get('/api/v1/trackers', params={'vehicle_client_id': cliente.id}).json()['total'] == 0
    assert http.get('/api/v1/trackers', params={'client_id': cliente.id}).json()['total'] == 1
    db.refresh(rastreador_instalado)
    assert rastreador_instalado.client_id == cliente.id


def test_vehicle_owner_filter_excludes_removed_vehicle(http, db, veiculo, cliente, rastreador_instalado):
    veiculo.is_deleted = True
    db.commit()
    assert http.get('/api/v1/trackers', params={'vehicle_client_id': cliente.id}).json()['total'] == 0


def test_plan_request_still_blocks_duplicate_contract(http, db, veiculo, contrato, rastreador_instalado):
    response = http.post(f'/api/v1/trackers/{rastreador_instalado.id}/link-vehicle',
                         json={'vehicle_id': veiculo.id, 'plan_id': contrato.plan_id})
    assert response.status_code == 409
    assert db.scalar(select(func.count()).select_from(Contract)) == 1
