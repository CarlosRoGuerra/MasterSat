"""Recuperação explícita de cadastros ativos; não importa obrigações financeiras."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.enums import VehicleStatus
from app.models.sgr_migracao import SgrVinculo
from app.models.vehicle import Vehicle
from app.schemas.client import ClientBase
from app.schemas.vehicle import VehicleOut, identifier_is_valid
from app.services.sgr_migration.client import ColetaPaginada, SGRClient, iterar_paginas
from app.services.sgr_migration.importer import _build_client, _build_vehicle
from app.services.sgr_migration.mapping import ci_get, map_cliente, map_veiculo
from app.services.sgr_migration.normalize import is_valid_cpf_cnpj, is_valid_plate, normalize_plate


class RecoveryError(ValueError):
    pass


@dataclass
class RecoverySource:
    vehicles: list[dict]
    clients: list[dict]


def _code(raw: dict, field: str) -> str:
    value = str(ci_get(raw, field) or '').strip()
    if not value or len(value) > 120:
        raise RecoveryError(f'Código de origem ausente ou inválido: {field}.')
    return value


def _status(raw: dict) -> str:
    return str(ci_get(raw, 'situacao_veiculo') or '').strip().upper()


def collect_source(client: SGRClient, identifiers: list[str]) -> RecoverySource:
    """Lê todas as páginas, sem filtrar a situação do responsável."""
    wanted = set(identifiers)
    vehicles = []
    collection = ColetaPaginada('/buscar_veiculo', 'todos')
    for page in iterar_paginas(
        lambda total, indice: client.extract_data(client.get('/buscar_veiculo', {'total': total, 'indice': indice})),
        200, collection,
    ):
        vehicles.extend(raw for raw in page if normalize_plate(str(ci_get(raw, 'placa_veiculo') or '')) in wanted)
    if not collection.completa or not collection.registros:
        raise RecoveryError('Consulta de veículos vazia ou incompleta.')
    owners = {_code(raw, 'cod_cliente') for raw in vehicles}
    customers = []
    collection = ColetaPaginada('/buscar_cliente', 'todos')
    for page in iterar_paginas(lambda total, indice: client.buscar_clientes(total=total, indice=indice), 200, collection):
        customers.extend(raw for raw in page if str(ci_get(raw, 'cod_cliente') or '').strip() in owners)
    if not collection.completa:
        raise RecoveryError('Consulta de clientes incompleta.')
    return RecoverySource(vehicles, customers)


def normalize_identifiers(identifiers: list[str]) -> list[str]:
    result = list(dict.fromkeys(normalize_plate(item) for item in identifiers))
    if not result or len(result) > 100 or any(not identifier_is_valid(item, True) for item in result):
        raise RecoveryError('Informe de 1 a 100 identificadores alfanuméricos com até 40 caracteres.')
    return result


def _origin(db: Session, entity: str, code: str) -> SgrVinculo | None:
    return db.scalar(select(SgrVinculo).where(SgrVinculo.entidade == entity, SgrVinculo.chave_origem == code))


def _register_origin(db: Session, entity: str, code: str, local_id: int, created: bool) -> None:
    local = db.scalar(select(SgrVinculo).where(SgrVinculo.entidade == entity, SgrVinculo.local_id == local_id))
    if local:
        if local.chave_origem != code:
            raise RecoveryError(f'{entity} #{local_id} pertence a outro código do SGR.')
        return
    db.add(SgrVinculo(entidade=entity, chave_origem=code, local_id=local_id,
                     criado_por='importacao' if created else 'adocao'))
    db.flush()


def recover_active_vehicles(db: Session, source: RecoverySource, identifiers: list[str], *, operator: str) -> list[dict]:
    """Uma transação para todo o lote. O chamador confirma ou desfaz a simulação.

    Conflito desfaz tudo; não transfere vínculo, reativa cliente ou sobrescreve
    veículos existentes. Use uma sessão dedicada à recuperação.
    """
    try:
        identifiers = normalize_identifiers(identifiers)
        if not operator.strip() or len(operator) > 120:
            raise RecoveryError('Informe o operador (até 120 caracteres).')
        if db.bind.dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(70820261008)'))
        by_identifier = defaultdict(list)
        by_customer = defaultdict(list)
        for raw in source.vehicles:
            by_identifier[normalize_plate(str(ci_get(raw, 'placa_veiculo') or ''))].append(raw)
        for raw in source.clients:
            by_customer[_code(raw, 'cod_cliente')].append(raw)
        prepared = []
        vehicle_codes = set()
        for identifier in identifiers:
            matches = by_identifier[identifier]
            if len(matches) != 1:
                raise RecoveryError(f'{identifier}: deve existir exatamente um veículo na origem.')
            raw = matches[0]
            if _status(raw) != 'ATIVO':
                raise RecoveryError(f'{identifier}: situação no SGR é {_status(raw) or "ausente"}, não ATIVO.')
            vehicle_code = _code(raw, 'cod_veiculo')
            if vehicle_code in vehicle_codes:
                raise RecoveryError('Mesmo código de veículo para identificadores diferentes.')
            vehicle_codes.add(vehicle_code)
            customer_code = _code(raw, 'cod_cliente')
            customers = by_customer[customer_code]
            if len(customers) != 1:
                raise RecoveryError(f'{identifier}: cliente de origem ausente ou duplicado.')
            customer_status = str(ci_get(ci_get(customers[0], 'situacao'), 'descricao') or '').strip().upper()
            if customer_status not in {'ATIVO', 'CADASTRADO', 'INATIVO', 'INADIMPLENTE', 'SUSPENSO', 'BLOQUEADO', 'CANCELADO'}:
                raise RecoveryError(f'{identifier}: situação do cliente de origem ausente ou desconhecida.')
            mapped_customer, _ = map_cliente(customers[0])
            customer = _build_client(mapped_customer)
            if not customer.name:
                raise RecoveryError(f'{identifier}: nome do cliente de origem ausente.')
            mapped_vehicle, _ = map_veiculo(raw)
            vehicle = _build_vehicle(mapped_vehicle, client_id=0)
            vehicle.plate = identifier
            vehicle.is_non_road_asset = not is_valid_plate(identifier)
            vehicle.status = VehicleStatus.ACTIVE
            # Falha antes de qualquer escrita; não expõe dados pessoais na mensagem.
            try:
                ClientBase.model_validate(customer, from_attributes=True)
                vehicle.id = 0
                VehicleOut.model_validate(vehicle)
                vehicle.id = None
            except ValidationError as exc:
                fields = ', '.join(str(err['loc'][0]) for err in exc.errors())
                raise RecoveryError(f'{identifier}: cadastro de origem inválido ({fields}).') from None
            prepared.append((vehicle_code, customer_code, customer, vehicle))

        result = []
        for vehicle_code, customer_code, candidate_customer, candidate in prepared:
            origin = _origin(db, 'cliente', customer_code)
            customers = db.scalars(select(Client).where(Client.cpf_cnpj == candidate_customer.cpf_cnpj)).all()
            if origin:
                customer = db.get(Client, origin.local_id)
                if not customer or customer.is_deleted or customer.cpf_cnpj != candidate_customer.cpf_cnpj:
                    raise RecoveryError(f'{candidate.plate}: identidade do cliente diverge da origem.')
            else:
                if len(customers) > 1 or any(item.is_deleted for item in customers):
                    raise RecoveryError(f'{candidate.plate}: cliente duplicado ou excluído; conferir manualmente.')
                customer = customers[0] if customers else None
            customer_created = customer is None
            if customer_created:
                if not is_valid_cpf_cnpj(candidate_customer.cpf_cnpj, candidate_customer.type):
                    raise RecoveryError(f'{candidate.plate}: documento do novo cliente inválido; corrigir na origem.')
                customer = candidate_customer
                db.add(customer)
                db.flush()
            _register_origin(db, 'cliente', customer_code, customer.id, customer_created)

            origin = _origin(db, 'veiculo', vehicle_code)
            vehicles = db.scalars(select(Vehicle).where(Vehicle.plate == candidate.plate)).all()
            if origin:
                vehicle = db.get(Vehicle, origin.local_id)
                if not vehicle or vehicle.plate != candidate.plate or vehicle.is_deleted:
                    raise RecoveryError(f'{candidate.plate}: identidade do veículo diverge da origem.')
            else:
                if len(vehicles) > 1 or any(item.is_deleted for item in vehicles):
                    raise RecoveryError(f'{candidate.plate}: veículo duplicado ou excluído; conferir manualmente.')
                vehicle = vehicles[0] if vehicles else None
            created = vehicle is None
            if not created and (vehicle.client_id != customer.id or vehicle.status != VehicleStatus.ACTIVE
                                or vehicle.is_non_road_asset != candidate.is_non_road_asset):
                raise RecoveryError(f'{candidate.plate}: veículo existente tem cliente, situação ou tipo divergente.')
            if created:
                vehicle = candidate
                vehicle.client_id = customer.id
                db.add(vehicle)
                db.flush()
                db.add(AuditLog(user_name=operator.strip(), method='IMPORT', path='/scripts/sgr_recuperar_veiculos_ativos',
                                entity_type='vehicle', entity_id=vehicle.id, status_code=201,
                                description=f'Recuperação de veículo ativo do SGR; código {vehicle_code}. Cliente SGR {customer_code}.'))
            _register_origin(db, 'veiculo', vehicle_code, vehicle.id, created)
            result.append({'identificador': vehicle.plate, 'vehicle_id': vehicle.id,
                           'situacao': vehicle.status.value, 'criado': created,
                           'equipamento_sem_placa': vehicle.is_non_road_asset,
                           'client_id': customer.id, 'cliente_criado': customer_created,
                           'documento_origem_valido': is_valid_cpf_cnpj(candidate_customer.cpf_cnpj, candidate_customer.type),
                           'situacao_cliente': customer.status.value})
        db.flush()
        return result
    except Exception:
        db.rollback()
        raise
