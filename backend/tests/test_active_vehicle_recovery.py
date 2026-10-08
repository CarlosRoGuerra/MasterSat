from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import ClientStatus
from app.models.sgr_migracao import SgrVinculo
from app.models.vehicle import Vehicle
from app.schemas.vehicle import VehicleOut
from app.services.sgr_migration.active_vehicle_recovery import RecoveryError, RecoverySource, recover_active_vehicles


IDENTIFIERS = ['ISCARF16017741', 'MOVEL3176', 'TRATOR', 'MHW0459']


def source():
    clients = [
        {'cod_cliente': '127', 'nome_cliente': 'Cliente ativo', 'cpf_cliente': '12345678909', 'situacao': {'descricao': 'ATIVO'}},
        {'cod_cliente': '192', 'nome_cliente': 'Cliente cancelado', 'cpf_cliente': '11144477735', 'situacao': {'descricao': 'CANCELADO'}},
    ]
    vehicles = [{'cod_veiculo': str(100 + index), 'cod_cliente': '192' if index == 3 else '127',
                 'placa_veiculo': identifier, 'situacao_veiculo': 'ATIVO'} for index, identifier in enumerate(IDENTIFIERS)]
    return RecoverySource(vehicles, clients)


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def recover(db, data=None):
    return recover_active_vehicles(db, data or source(), IDENTIFIERS, operator='administrator')


def test_preserves_identifiers_owners_status_and_has_no_financial_side_effects(db):
    result = recover(db)
    db.commit()
    assert len(result) == 4
    assert all(item['criado'] and item['situacao'] == 'ativo' for item in result)
    assert [item['equipamento_sem_placa'] for item in result] == [True, True, True, False]
    assert result[-1]['situacao_cliente'] == 'inativo'
    assert count(db, Client) == 2
    assert count(db, SgrVinculo) == 6
    assert count(db, AuditLog) == 4
    assert count(db, Billing) == count(db, Contract) == 0
    assert [VehicleOut.model_validate(item).plate for item in db.scalars(select(Vehicle).order_by(Vehicle.id))] == IDENTIFIERS


def test_second_run_reuses_cadastros_and_audit(db):
    first = recover(db)
    db.commit()
    second = recover(db)
    db.commit()
    assert [item['vehicle_id'] for item in first] == [item['vehicle_id'] for item in second]
    assert not any(item['criado'] or item['cliente_criado'] for item in second)
    assert count(db, Vehicle) == count(db, AuditLog) == 4


def test_simulation_rolls_back_cadastros_provenance_and_audit(db):
    recover(db)
    db.rollback()
    assert count(db, Client) == count(db, Vehicle) == count(db, SgrVinculo) == count(db, AuditLog) == 0


def test_existing_client_status_and_details_are_preserved(db):
    client = Client(name='Nome local', cpf_cnpj='12345678909', status=ClientStatus.SUSPENDED)
    db.add(client)
    db.commit()
    result = recover(db)
    db.commit()
    db.refresh(client)
    assert client.name == 'Nome local'
    assert client.status == ClientStatus.SUSPENDED
    assert result[0]['client_id'] == client.id and not result[0]['cliente_criado']


@pytest.mark.parametrize('mutation', ['inactive', 'missing', 'duplicate', 'missing_owner', 'bad_document'])
def test_source_problem_blocks_whole_batch(db, mutation):
    data = deepcopy(source())
    if mutation == 'inactive':
        data.vehicles[-1]['situacao_veiculo'] = 'CANCELADO'
    elif mutation == 'missing':
        data.vehicles.pop()
    elif mutation == 'duplicate':
        data.vehicles.append(data.vehicles[-1].copy())
    elif mutation == 'missing_owner':
        data.clients.pop()
    else:
        data.clients[-1]['cpf_cliente'] = '11111111111'
    with pytest.raises(RecoveryError):
        recover(db, data)
    assert count(db, Client) == count(db, Vehicle) == count(db, AuditLog) == count(db, SgrVinculo) == 0


def test_last_vehicle_conflict_rolls_back_earlier_inserts(db, cliente):
    existing = Vehicle(client_id=cliente.id, plate='MHW0459')
    db.add(existing)
    db.commit()
    with pytest.raises(RecoveryError, match='cliente, situação ou tipo divergente'):
        recover(db)
    assert count(db, Client) == count(db, Vehicle) == 1
    assert count(db, AuditLog) == count(db, SgrVinculo) == 0


def test_deleted_vehicle_is_not_recreated(db, cliente):
    existing = Vehicle(client_id=cliente.id, plate='TRATOR', is_non_road_asset=True, is_deleted=True)
    db.add(existing)
    db.commit()
    with pytest.raises(RecoveryError, match='excluído'):
        recover(db)
    assert count(db, Vehicle) == 1
    assert count(db, SgrVinculo) == 0


def test_existing_legacy_document_is_reused_without_changing_it(db):
    data = source()
    data.clients[0]['cpf_cliente'] = '11111111111'
    client = Client(name='Nome local', cpf_cnpj='11111111111')
    db.add(client)
    db.commit()
    result = recover(db, data)
    db.commit()
    assert result[0]['documento_origem_valido'] is False
    assert result[0]['client_id'] == client.id
    db.refresh(client)
    assert client.name == 'Nome local' and client.cpf_cnpj == '11111111111'


def test_origin_bound_to_different_customer_is_not_adopted(db):
    customer = Client(name='Outro código', cpf_cnpj='12345678909')
    db.add(customer)
    db.flush()
    db.add(SgrVinculo(entidade='cliente', chave_origem='999', local_id=customer.id, criado_por='importacao'))
    db.commit()
    with pytest.raises(RecoveryError, match='outro código'):
        recover(db)
    assert count(db, Client) == count(db, SgrVinculo) == 1
    assert count(db, Vehicle) == count(db, AuditLog) == 0


def test_unknown_owner_status_does_not_default_to_active(db):
    data = source()
    data.clients[0]['situacao'] = {'descricao': 'DESCONHECIDO'}
    with pytest.raises(RecoveryError, match='situação do cliente'):
        recover(db, data)
    assert count(db, Client) == count(db, Vehicle) == 0


def test_collection_requires_complete_source(mocker):
    from app.services.sgr_migration.active_vehicle_recovery import collect_source
    def incomplete(fetch, page_size, collection):
        collection.registros = 1
        yield source().vehicles
    mocker.patch('app.services.sgr_migration.active_vehicle_recovery.iterar_paginas', incomplete)
    with pytest.raises(RecoveryError, match='incompleta'):
        collect_source(mocker.Mock(), IDENTIFIERS)


def test_collection_includes_cancelled_customer_and_filters_explicit_identifiers(mocker):
    from app.services.sgr_migration.active_vehicle_recovery import collect_source
    data = source()
    extra = dict(data.vehicles[0], placa_veiculo='ARA9119', cod_cliente='888')
    def complete(fetch, page_size, collection):
        collection.completa = True
        collection.registros = 5
        yield [*data.vehicles, extra] if collection.endpoint == '/buscar_veiculo' else data.clients
    mocker.patch('app.services.sgr_migration.active_vehicle_recovery.iterar_paginas', complete)
    result = collect_source(mocker.Mock(), IDENTIFIERS)
    assert len(result.vehicles) == 4
    assert result.clients[-1]['situacao']['descricao'] == 'CANCELADO'
