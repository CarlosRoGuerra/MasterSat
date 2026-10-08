import pytest
from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.models.vehicle import Vehicle


@pytest.mark.parametrize('identifier', ['ISCARF16017741', 'MOVEL3176', 'TRATOR'])
def test_asset_create_list_edit_round_trip(http, cliente, identifier):
    response = http.post('/api/v1/vehicles', json={'client_id': cliente.id, 'plate': identifier, 'is_non_road_asset': True})
    assert response.status_code == 200, response.text
    vehicle_id = response.json()['id']
    assert response.json()['plate'] == identifier
    assert response.json()['is_non_road_asset'] is True
    response = http.get('/api/v1/vehicles', params={'search': identifier})
    assert response.status_code == 200, response.text
    assert response.json()['items'][0]['plate'] == identifier
    response = http.put(f'/api/v1/vehicles/{vehicle_id}', json={'plate': identifier, 'is_non_road_asset': True, 'model': 'Máquina'})
    assert response.status_code == 200, response.text
    assert response.json()['plate'] == identifier
    assert http.put(f'/api/v1/vehicles/{vehicle_id}', json={'is_non_road_asset': False}).status_code == 422


def test_regular_validation_still_requires_plate(http, cliente):
    assert http.post('/api/v1/vehicles', json={'client_id': cliente.id, 'plate': 'GEISON'}).status_code == 422
    assert http.post('/api/v1/vehicles', json={'client_id': cliente.id, 'plate': 'ISCARF16017741'}).status_code == 422
    assert http.post('/api/v1/vehicles', json={'client_id': cliente.id, 'plate': 'ABCD123'}).status_code == 200


def test_preview_then_delete_excludes_linked_and_missing_preserves_history(http, db, veiculo, rastreador_instalado, cliente):
    free = Vehicle(client_id=cliente.id, plate='TRATOR', is_non_road_asset=True)
    db.add(free)
    db.commit()
    ids = [free.id, veiculo.id, 9999, free.id]
    preview = http.post('/api/v1/vehicles/lote/excluir', json={'ids': ids, 'simular': True})
    assert preview.status_code == 200, preview.text
    assert preview.json()['aplicados'] == 1 and preview.json()['ignorados'] == 2
    assert preview.json()['total_enviados'] == 3
    db.refresh(free)
    assert not free.is_deleted
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.method == 'DELETE')) == 0
    response = http.post('/api/v1/vehicles/lote/excluir', json={'ids': ids})
    assert response.status_code == 200, response.text
    assert response.json()['aplicados'] == 1
    db.refresh(free)
    db.refresh(veiculo)
    assert free.is_deleted and not veiculo.is_deleted
    assert db.get(Vehicle, free.id) is not None
    assert http.get('/api/v1/vehicles', params={'search': 'TRATOR'}).json()['items'] == []
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.method == 'DELETE')) == 1
    assert http.post('/api/v1/vehicles/lote/excluir', json={'ids': [free.id]}).json()['aplicados'] == 0


def test_batch_rechecks_new_tracker_after_preview(http, db, veiculo, rastreador):
    assert http.post('/api/v1/vehicles/lote/excluir', json={'ids': [veiculo.id], 'simular': True}).json()['aplicados'] == 1
    rastreador.vehicle_id = veiculo.id
    db.commit()
    assert http.post('/api/v1/vehicles/lote/excluir', json={'ids': [veiculo.id]}).json()['aplicados'] == 0
    db.refresh(veiculo)
    assert not veiculo.is_deleted


def test_active_contract_blocks_single_and_batch_even_without_tracker(http, db, contrato, rastreador_instalado, veiculo):
    rastreador_instalado.vehicle_id = None
    db.commit()
    result = http.post('/api/v1/vehicles/lote/excluir', json={'ids': [veiculo.id]})
    assert result.json()['aplicados'] == 0
    assert 'contrato ativo' in result.json()['itens'][0]['motivo']
    assert http.delete(f'/api/v1/vehicles/{veiculo.id}').status_code == 400


@pytest.mark.parametrize('fixture_name', ['http_op', 'http_fin', 'http_cliente'])
def test_bulk_delete_admin_only(request, fixture_name, veiculo, db):
    client = request.getfixturevalue(fixture_name)
    assert client.post('/api/v1/vehicles/lote/excluir', json={'ids': [veiculo.id]}).status_code == 403
    db.refresh(veiculo)
    assert not veiculo.is_deleted


@pytest.mark.parametrize('ids', [[], [0], [-1], list(range(1, 2002))])
def test_invalid_selection_is_rejected(http, ids):
    assert http.post('/api/v1/vehicles/lote/excluir', json={'ids': ids}).status_code == 422
