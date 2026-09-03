"""Busca Global (Command Palette) — agrega Clientes, Veículos, Rastreadores,
Ordens de Serviço, Contratos e Documentos numa única resposta.

Reaproveita o mesmo padrão de busca já usado em cada listagem (ilike sobre
or_(...), soft-delete via is_deleted) — ver app/api/v1/endpoints/clients.py,
vehicles.py, trackers.py, service_orders.py, contracts.py. A diferença é que
aqui cada categoria roda com um LIMIT baixo e o termo é normalizado uma vez
só para todas elas.

CPF/CNPJ, telefone e IMEI já são salvos só com dígitos (ver os
field_validator em app/schemas/client.py e app/schemas/tracker.py) e a placa
já é salva maiúscula sem traço/espaço (app/schemas/vehicle.py) — por isso a
busca por esses campos é só normalizar o termo digitado, sem precisar de
função de banco. Nome/marca/modelo usam unaccent() para tolerar acento —
função real no Postgres (extensão, ver migration), registrada como função
Python no SQLite de teste (ver app/db/session.py) para o comportamento ser
idêntico nos dois bancos.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, literal, or_, select
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.contract import Contract
from app.models.document import Document
from app.models.enums import UserRole
from app.models.plan import Plan
from app.models.service_order import ServiceOrder
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.schemas.search import GlobalSearchOut, SearchResultItem

STAFF_ROLES = (UserRole.ADMIN, UserRole.OPERATIONAL, UserRole.FINANCIAL)
CONTRACT_ROLES = (UserRole.ADMIN, UserRole.FINANCIAL)

DEFAULT_LIMIT = 6
MIN_QUERY_LENGTH = 2
MIN_DIGITS_LENGTH = 3


@dataclass(frozen=True)
class SearchTerms:
    raw: str
    ilike_term: str
    digits: str
    plate: str


def _digits(value: str) -> str:
    return ''.join(filter(str.isdigit, value))


def _normalize_plate(value: str) -> str:
    return value.strip().upper().replace('-', '').replace(' ', '')


def _build_terms(raw: str) -> SearchTerms:
    digits = _digits(raw)
    return SearchTerms(
        raw=raw,
        ilike_term=f'%{raw}%',
        digits=digits if len(digits) >= MIN_DIGITS_LENGTH else '',
        plate=_normalize_plate(raw),
    )


def _unaccent_ilike(column, raw: str):
    """ilike acento-insensível — ver docstring do módulo."""
    return func.unaccent(column).ilike(func.unaccent(literal(f'%{raw}%')))


def _enum_value(value) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, 'value') else str(value)


def _search_clients(db: Session, terms: SearchTerms, limit: int) -> list[SearchResultItem]:
    conditions = [
        _unaccent_ilike(Client.name, terms.raw),
        _unaccent_ilike(Client.trade_name, terms.raw),
        Client.email.ilike(terms.ilike_term),
    ]
    if terms.digits:
        conditions.append(Client.cpf_cnpj.ilike(f'%{terms.digits}%'))
        conditions.append(Client.phone.ilike(f'%{terms.digits}%'))

    stmt = (
        select(Client)
        .where(Client.is_deleted.is_(False), or_(*conditions))
        .order_by(Client.id.desc())
        .limit(limit)
    )
    rows = db.scalars(stmt).all()
    return [
        SearchResultItem(
            id=c.id,
            entity='client',
            title=c.name,
            subtitle=c.cpf_cnpj,
            status=_enum_value(c.status),
        )
        for c in rows
    ]


def _search_vehicles(db: Session, terms: SearchTerms, limit: int) -> list[SearchResultItem]:
    conditions = [
        Vehicle.plate.ilike(f'%{terms.plate}%'),
        _unaccent_ilike(Vehicle.brand, terms.raw),
        _unaccent_ilike(Vehicle.model, terms.raw),
        Vehicle.contract_number.ilike(terms.ilike_term),
        # Buscar pelo nome do dono também acha o veículo — ex.: digitar
        # "João" mostra o(s) veículo(s) dele junto com o cliente.
        _unaccent_ilike(Client.name, terms.raw),
    ]
    if terms.digits:
        conditions.append(Vehicle.chassis.ilike(f'%{terms.digits}%'))
        conditions.append(Vehicle.renavam.ilike(f'%{terms.digits}%'))

    stmt = (
        select(Vehicle, Client.name.label('client_name'))
        .join(Client, Client.id == Vehicle.client_id)
        .where(Vehicle.is_deleted.is_(False), or_(*conditions))
        .order_by(Vehicle.id.desc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    items = []
    for v, client_name in rows:
        model_label = ' '.join(part for part in (v.brand, v.model) if part)
        subtitle = f'{model_label} — {client_name}' if model_label else client_name
        items.append(
            SearchResultItem(
                id=v.id,
                entity='vehicle',
                title=v.plate,
                subtitle=subtitle,
                status=_enum_value(v.status),
                client_id=v.client_id,
            )
        )
    return items


def _search_trackers(db: Session, terms: SearchTerms, limit: int) -> list[SearchResultItem]:
    conditions = [
        Tracker.serial_number.ilike(terms.ilike_term),
        _unaccent_ilike(Tracker.brand, terms.raw),
        _unaccent_ilike(Tracker.model, terms.raw),
        Vehicle.plate.ilike(f'%{terms.plate}%'),
        _unaccent_ilike(Client.name, terms.raw),
    ]
    if terms.digits:
        conditions.append(Tracker.imei.ilike(f'%{terms.digits}%'))
        conditions.append(Tracker.sim_number.ilike(f'%{terms.digits}%'))

    stmt = (
        select(Tracker, Vehicle.plate.label('vehicle_plate'))
        .outerjoin(Vehicle, Vehicle.id == Tracker.vehicle_id)
        .outerjoin(Client, Client.id == Tracker.client_id)
        .where(Tracker.is_deleted.is_(False), or_(*conditions))
        .order_by(Tracker.id.desc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    return [
        SearchResultItem(
            id=t.id,
            entity='tracker',
            title=f'IMEI {t.imei}',
            subtitle=f'Veículo {vehicle_plate}' if vehicle_plate else 'Sem veículo vinculado',
            status=_enum_value(t.status),
            client_id=t.client_id,
            vehicle_id=t.vehicle_id,
        )
        for t, vehicle_plate in rows
    ]


def _search_service_orders(db: Session, terms: SearchTerms, limit: int) -> list[SearchResultItem]:
    conditions = [
        ServiceOrder.number.ilike(terms.ilike_term),
        _unaccent_ilike(Client.name, terms.raw),
        Vehicle.plate.ilike(f'%{terms.plate}%'),
    ]
    if terms.digits:
        conditions.append(Tracker.imei.ilike(f'%{terms.digits}%'))

    stmt = (
        select(ServiceOrder, Client.name.label('client_name'), Vehicle.plate.label('vehicle_plate'))
        .join(Client, Client.id == ServiceOrder.client_id)
        .outerjoin(Vehicle, Vehicle.id == ServiceOrder.vehicle_id)
        .outerjoin(Tracker, Tracker.id == ServiceOrder.tracker_id)
        .where(ServiceOrder.is_deleted.is_(False), or_(*conditions))
        .order_by(ServiceOrder.id.desc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    return [
        SearchResultItem(
            id=o.id,
            entity='service_order',
            title=o.number,
            subtitle=f'{client_name} — {vehicle_plate}' if vehicle_plate else client_name,
            status=_enum_value(o.status),
            client_id=o.client_id,
        )
        for o, client_name, vehicle_plate in rows
    ]


def _search_contracts(db: Session, terms: SearchTerms, limit: int) -> list[SearchResultItem]:
    client_ids = select(Client.id).where(Client.is_deleted.is_(False), _unaccent_ilike(Client.name, terms.raw))
    vehicle_ids = select(Vehicle.id).where(Vehicle.is_deleted.is_(False), Vehicle.plate.ilike(f'%{terms.plate}%'))
    conditions = [Contract.client_id.in_(client_ids), Contract.vehicle_id.in_(vehicle_ids)]

    stmt = (
        select(Contract, Client.name.label('client_name'), Plan.name.label('plan_name'), Vehicle.plate.label('vehicle_plate'))
        .join(Client, Client.id == Contract.client_id)
        .join(Plan, Plan.id == Contract.plan_id)
        .outerjoin(Vehicle, Vehicle.id == Contract.vehicle_id)
        .where(Contract.is_deleted.is_(False), or_(*conditions))
        .order_by(Contract.id.desc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    return [
        SearchResultItem(
            id=c.id,
            entity='contract',
            title=f'Contrato #{c.id} — {client_name}',
            subtitle=f'{plan_name} — {vehicle_plate}' if vehicle_plate else plan_name,
            status=c.status,
            client_id=c.client_id,
        )
        for c, client_name, plan_name, vehicle_plate in rows
    ]


def _search_documents(db: Session, terms: SearchTerms, limit: int) -> list[SearchResultItem]:
    stmt = (
        select(Document)
        .where(
            Document.active.is_(True),
            or_(
                _unaccent_ilike(Document.file_name, terms.raw),
                Document.category.ilike(terms.ilike_term),
            ),
        )
        .order_by(Document.id.desc())
        .limit(limit)
    )
    docs = db.scalars(stmt).all()
    if not docs:
        return []

    client_ids = {d.reference_id for d in docs if d.reference_type == 'client'}
    vehicle_ids = {d.reference_id for d in docs if d.reference_type == 'vehicle'}
    order_ids = {d.reference_id for d in docs if d.reference_type == 'service_order'}

    # Só dono ativo — um documento de cliente/veículo/OS já excluído (soft
    # delete) não pode virar link morto no resultado da busca.
    clients = (
        {c.id: c for c in db.scalars(select(Client).where(Client.id.in_(client_ids), Client.is_deleted.is_(False)))}
        if client_ids else {}
    )
    vehicles = (
        {v.id: v for v in db.scalars(select(Vehicle).where(Vehicle.id.in_(vehicle_ids), Vehicle.is_deleted.is_(False)))}
        if vehicle_ids else {}
    )
    orders = (
        {o.id: o for o in db.scalars(select(ServiceOrder).where(ServiceOrder.id.in_(order_ids), ServiceOrder.is_deleted.is_(False)))}
        if order_ids else {}
    )

    items: list[SearchResultItem] = []
    for d in docs:
        client_id = vehicle_id = service_order_id = None
        subtitle = None
        # client_id/vehicle_id/service_order_id só são preenchidos quando o
        # dono ainda existe e está ativo — senão o frontend tentaria navegar
        # para um registro já excluído (link morto).
        if d.reference_type == 'client':
            parent = clients.get(d.reference_id)
            if parent:
                client_id = parent.id
                subtitle = f'Cliente: {parent.name}'
            else:
                subtitle = 'Cliente removido'
        elif d.reference_type == 'vehicle':
            parent = vehicles.get(d.reference_id)
            if parent:
                vehicle_id = parent.id
                client_id = parent.client_id
                subtitle = f'Veículo: {parent.plate}'
            else:
                subtitle = 'Veículo removido'
        elif d.reference_type == 'service_order':
            parent = orders.get(d.reference_id)
            if parent:
                service_order_id = parent.id
                client_id = parent.client_id
                subtitle = f'OS: {parent.number}'
            else:
                subtitle = 'Ordem de serviço removida'

        items.append(
            SearchResultItem(
                id=d.id,
                entity='document',
                title=d.file_name,
                subtitle=subtitle,
                status=d.category,
                client_id=client_id,
                vehicle_id=vehicle_id,
                service_order_id=service_order_id,
            )
        )
    return items


def run_global_search(db: Session, role: UserRole, q: str, limit: int = DEFAULT_LIMIT) -> GlobalSearchOut:
    raw = (q or '').strip()
    if len(raw) < MIN_QUERY_LENGTH:
        return GlobalSearchOut()

    terms = _build_terms(raw)
    result = GlobalSearchOut(
        clients=_search_clients(db, terms, limit),
        vehicles=_search_vehicles(db, terms, limit),
        trackers=_search_trackers(db, terms, limit),
        service_orders=_search_service_orders(db, terms, limit),
        documents=_search_documents(db, terms, limit),
    )
    if role in CONTRACT_ROLES:
        result.contracts = _search_contracts(db, terms, limit)
    return result
