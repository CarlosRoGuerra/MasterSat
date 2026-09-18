"""
Importação dos dados do SGR para o banco do MasterSat.

Diferente do restante do módulo (que é só leitura), este arquivo ESCREVE —
mas só é chamado pelo scripts/sgr_import.py, que exige --apply explícito e
trava em banco que não seja local.

Idempotência por CHAVE NATURAL (cpf_cnpj do cliente, placa do veículo, IMEI
do rastreador), que já têm índice único parcial nos models. Rodar duas vezes
não duplica: o registro existente é reaproveitado, nunca sobrescrito — este
importador só INSERE o que falta, jamais altera dado que já está no
MasterSat. Continua sendo o SGR quem manda enquanto a migração não é
definitiva; sobrescrever aqui poderia apagar correção feita à mão.

Limitação assumida: sem coluna external_id nos models, não há como amarrar o
registro ao código de origem do SGR. A chave natural cobre a re-execução,
mas um cliente que troque de CPF no SGR entraria como novo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.enums import ClientStatus, TrackerStatus, VehicleStatus
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.services.sgr_migration.normalize import is_valid_plate
from app.services.sgr_migration.poc import ClientNode, PocRunResult


@dataclass
class ImportStats:
    clients_created: int = 0
    clients_reused: int = 0
    clients_skipped: int = 0
    vehicles_created: int = 0
    vehicles_reused: int = 0
    vehicles_skipped: int = 0
    trackers_created: int = 0
    trackers_reused: int = 0
    trackers_skipped: int = 0
    skips: list[str] = field(default_factory=list)

    def skip(self, motivo: str) -> None:
        self.skips.append(motivo)


def _trunc(value, limit: int) -> str | None:
    """Corta no limite da coluna — o SGR não garante os tamanhos do MasterSat."""
    if value in (None, ''):
        return None
    return str(value).strip()[:limit]


def _renavam_valido(value) -> str | None:
    """RENAVAM só entra se tiver 9-11 dígitos (regra do schema da API).

    A base do SGR usa placeholders ('00000000000000000000', '000089') que não
    são RENAVAM nenhum. Gravá-los faz o GET /vehicles estourar na validação
    da RESPOSTA e derruba a listagem inteira — o campo é opcional, então o
    certo é entrar vazio em vez de entrar inválido.
    """
    digits = ''.join(filter(str.isdigit, str(value or '')))
    return digits if len(digits) in (9, 10, 11) else None


def _chassi_valido(value) -> str | None:
    """Chassi só entra com 8+ caracteres (regra do schema da API)."""
    if not value:
        return None
    limpo = str(value).strip().upper().replace(' ', '')
    return limpo if len(limpo) >= 8 else None


def _cep_valido(value) -> str | None:
    """CEP só entra com exatamente 8 dígitos (regra do schema da API)."""
    digits = ''.join(filter(str.isdigit, str(value or '')))
    return digits if len(digits) == 8 else None


def _to_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except ValueError:
        return None


def _find_client(db: Session, cpf_cnpj: str) -> Client | None:
    return (
        db.query(Client)
        .filter(Client.cpf_cnpj == cpf_cnpj, Client.is_deleted.is_(False))
        .first()
    )


def _find_vehicle(db: Session, plate: str) -> Vehicle | None:
    return (
        db.query(Vehicle)
        .filter(Vehicle.plate == plate, Vehicle.is_deleted.is_(False))
        .first()
    )


def _find_tracker(db: Session, imei: str) -> Tracker | None:
    return (
        db.query(Tracker)
        .filter(Tracker.imei == imei, Tracker.is_deleted.is_(False))
        .first()
    )


def _build_client(mapped: dict) -> Client:
    return Client(
        name=_trunc(mapped.get('name'), 180),
        trade_name=_trunc(mapped.get('trade_name'), 180),
        cpf_cnpj=_trunc(mapped.get('cpf_cnpj'), 18),
        type=mapped.get('type') or 'pf',
        status=ClientStatus(mapped.get('status') or ClientStatus.ACTIVE.value),
        email=_trunc(mapped.get('email'), 180),
        extra_emails=mapped.get('extra_emails') or None,
        phone=_trunc(mapped.get('phone'), 30),
        contacts=mapped.get('contacts') or None,
        zip_code=_trunc(mapped.get('zip_code'), 12),
        address_line=_trunc(mapped.get('address_line'), 180),
        address_number=_trunc(mapped.get('address_number'), 30),
        address_complement=_trunc(mapped.get('address_complement'), 120),
        neighborhood=_trunc(mapped.get('neighborhood'), 120),
        city=_trunc(mapped.get('city'), 120),
        state=_trunc(mapped.get('state'), 2),
        rg_ie=_trunc(mapped.get('rg_ie'), 30),
        birth_date=_to_date(mapped.get('birth_date')),
        notes='Importado do SGR (Hinova).',
    )


def _build_vehicle(mapped: dict, client_id: int) -> Vehicle:
    return Vehicle(
        client_id=client_id,
        plate=_trunc(mapped.get('plate'), 10),
        chassis=_chassi_valido(mapped.get('chassis')),
        renavam=_renavam_valido(mapped.get('renavam')),
        brand=_trunc(mapped.get('brand'), 80),
        model=_trunc(mapped.get('model'), 120),
        color=_trunc(mapped.get('color'), 50),
        fuel_type=_trunc(mapped.get('fuel_type'), 40),
        type=_trunc(mapped.get('type'), 30),
        manufacture_year=mapped.get('manufacture_year'),
        model_year=mapped.get('model_year'),
        year=mapped.get('model_year') or mapped.get('manufacture_year'),
        fipe_code=_trunc(mapped.get('fipe_code'), 30),
        fipe_value=mapped.get('fipe_value'),
        contract_number=_trunc(mapped.get('contract_number'), 60),
        contract_date=_to_date(mapped.get('contract_date')),
        contract_end_date=_to_date(mapped.get('contract_end_date')),
        address_zip_code=_cep_valido(mapped.get('address_zip_code')),
        address_line=_trunc(mapped.get('address_line'), 255),
        address_number=_trunc(mapped.get('address_number'), 30),
        address_complement=_trunc(mapped.get('address_complement'), 120),
        neighborhood=_trunc(mapped.get('neighborhood'), 120),
        city=_trunc(mapped.get('city'), 120),
        state=_trunc(mapped.get('state'), 2),
        sales_point=_trunc(mapped.get('sales_point'), 120),
        seller_consultant=_trunc(mapped.get('seller_consultant'), 120),
        vehicle_classification=_trunc(mapped.get('vehicle_classification'), 80),
        user_alert=mapped.get('user_alert'),
        status=VehicleStatus(mapped.get('status') or VehicleStatus.ACTIVE.value),
    )


def _build_tracker(mapped: dict, client_id: int, vehicle_id: int) -> Tracker:
    return Tracker(
        imei=_trunc(mapped.get('imei'), 50),
        serial_number=_trunc(mapped.get('serial_number'), 60),
        model=_trunc(mapped.get('model'), 60),
        status=TrackerStatus(mapped.get('status') or TrackerStatus.INSTALLED.value),
        sim_number=_trunc(mapped.get('sim_number'), 30),
        install_date=_to_date(mapped.get('install_date')),
        installation_fee=mapped.get('installation_fee'),
        client_id=client_id,
        vehicle_id=vehicle_id,
        notes='Importado do SGR (Hinova).',
    )


def _import_client(db: Session, node: ClientNode, stats: ImportStats) -> Client | None:
    mapped = node.mapped
    cpf_cnpj = mapped.get('cpf_cnpj')
    if not cpf_cnpj or not mapped.get('name'):
        stats.clients_skipped += 1
        stats.skip(f"cliente SGR #{mapped.get('external_id')}: sem nome ou sem CPF/CNPJ — obrigatórios no MasterSat")
        return None

    existing = _find_client(db, cpf_cnpj)
    if existing:
        stats.clients_reused += 1
        return existing

    client = _build_client(mapped)
    db.add(client)
    db.flush()  # precisa do id para os veículos
    stats.clients_created += 1
    return client


def import_poc_result(db: Session, result: PocRunResult, dry_run: bool = True) -> ImportStats:
    """Insere no MasterSat os clientes/veículos/rastreadores já lidos do SGR.

    Em `dry_run` tudo roda dentro da transação e sofre rollback no final —
    serve para ver exatamente o que seria criado, inclusive erros de
    constraint, sem deixar nada no banco.
    """
    stats = ImportStats()

    for node in result.clients:
        if node.fetch_failed:
            stats.clients_skipped += 1
            stats.skip(f"cliente SGR #{node.mapped.get('external_id')}: falhou no mapeamento")
            continue

        client = _import_client(db, node, stats)
        if client is None:
            continue

        for vnode in node.vehicles:
            plate = vnode.mapped.get('plate')
            if not plate:
                stats.vehicles_skipped += 1
                stats.skip(f"veículo SGR #{vnode.mapped.get('external_id')}: sem placa")
                continue
            if not is_valid_plate(plate):
                # O MasterSat exige placa de 7 caracteres no schema — inclusive
                # ao SERIALIZAR a resposta. Um registro assim não só é recusado
                # no cadastro: ele derruba o GET /vehicles inteiro. Até existir
                # suporte a ativo sem placa (máquina pesada), fica de fora.
                stats.vehicles_skipped += 1
                stats.skip(
                    f"veículo SGR #{vnode.mapped.get('external_id')}: placa '{plate}' fora do padrão "
                    f"(máquina sem placa ou erro de cadastro) — o modelo do MasterSat não comporta"
                )
                continue

            vehicle = _find_vehicle(db, plate)
            if vehicle:
                stats.vehicles_reused += 1
            else:
                vehicle = _build_vehicle(vnode.mapped, client.id)
                db.add(vehicle)
                db.flush()
                stats.vehicles_created += 1

            for tnode in vnode.trackers:
                imei = tnode.mapped.get('imei')
                if not imei:
                    stats.trackers_skipped += 1
                    stats.skip(
                        f"rastreador do veículo {plate}: sem IMEI (obrigatório no MasterSat)"
                    )
                    continue
                if _find_tracker(db, imei):
                    stats.trackers_reused += 1
                    continue
                db.add(_build_tracker(tnode.mapped, client.id, vehicle.id))
                db.flush()
                stats.trackers_created += 1

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return stats
