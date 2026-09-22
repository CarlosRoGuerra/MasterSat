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

from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus, ClientStatus, TrackerStatus, VehicleStatus
from app.models.plan import Plan
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
    plans_created: int = 0
    plans_reused: int = 0
    plans_skipped: int = 0
    contracts_created: int = 0
    contracts_reused: int = 0
    contracts_skipped: int = 0
    billings_created: int = 0
    billings_reused: int = 0
    billings_skipped: int = 0
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


def _import_plans(db: Session, result: PocRunResult, stats: ImportStats) -> dict[str, Plan]:
    """Cria os Planos vindos de /get_grupo_mensalidade.

    Chave natural: o NOME do plano (coluna unique em plans). O retorno é um
    índice cod_grupo_mensalidade -> Plan, que é como o vínculo referencia o
    plano (`cod_grupo_vinculo`).
    """
    por_codigo: dict[str, Plan] = {}
    for mapped in result.plans:
        nome = mapped.get('name')
        codigo = str(mapped.get('external_id') or '')
        if not nome or mapped.get('price') is None:
            stats.plans_skipped += 1
            stats.skip(f'plano SGR #{codigo}: sem nome ou sem valor — obrigatórios no MasterSat')
            continue

        existente = (
            db.query(Plan).filter(Plan.name == nome, Plan.is_deleted.is_(False)).first()
        )
        if existente:
            stats.plans_reused += 1
            por_codigo[codigo] = existente
            continue

        plano = Plan(
            name=_trunc(nome, 120),
            price=mapped['price'],
            billing_interval_months=mapped.get('billing_interval_months') or 1,
            description='Importado do SGR (Hinova).',
            active=True,
        )
        db.add(plano)
        db.flush()
        stats.plans_created += 1
        por_codigo[codigo] = plano
    return por_codigo


def _find_contract(db: Session, client_id: int, vehicle_id: int) -> Contract | None:
    """Dedup de contrato.

    Sem external_id nos models, a chave possível é (cliente, veículo): no SGR
    a cobrança é configurada por vínculo, e um veículo ativo tem um vínculo
    vigente. Se um dia existir mais de um contrato por veículo (renovação
    registrada como novo vínculo), esta regra reaproveita o primeiro em vez
    de duplicar — que é o comportamento seguro para um importador.
    """
    return (
        db.query(Contract)
        .filter(
            Contract.client_id == client_id,
            Contract.vehicle_id == vehicle_id,
            Contract.is_deleted.is_(False),
        )
        .first()
    )


def _import_contract(
    db: Session,
    contrato: dict,
    client_id: int,
    vehicle_id: int,
    tracker_id: int | None,
    plano_por_codigo: dict[str, Plan],
    stats: ImportStats,
    placa: str,
) -> None:
    plan_code = contrato.get('plan_external_id')
    plano = plano_por_codigo.get(str(plan_code)) if plan_code else None
    if plano is None:
        stats.contracts_skipped += 1
        stats.skip(
            f'contrato do veículo {placa}: grupo de mensalidade {plan_code!r} não existe em '
            f'/get_grupo_mensalidade (plano descontinuado?) — contrato não criado'
        )
        return

    if not contrato.get('start_date'):
        stats.contracts_skipped += 1
        stats.skip(f'contrato do veículo {placa}: sem data de início (obrigatória no MasterSat)')
        return

    if _find_contract(db, client_id, vehicle_id):
        stats.contracts_reused += 1
        return

    interveniente_id = None
    cpf_interveniente = contrato.get('interveniente_cpf')
    if cpf_interveniente:
        outro = _find_client(db, cpf_interveniente)
        # Interveniente igual ao próprio cliente é o caso normal no SGR e no
        # MasterSat significa "sem interveniente" (a coluna fica nula).
        if outro and outro.id != client_id:
            interveniente_id = outro.id

    db.add(Contract(
        client_id=client_id,
        vehicle_id=vehicle_id,
        tracker_id=tracker_id,
        plan_id=plano.id,
        interveniente_client_id=interveniente_id,
        start_date=_to_date(contrato.get('start_date')),
        billing_day=contrato.get('billing_day'),
        status=contrato.get('status') or 'ativo',
        billing_modality=contrato.get('billing_modality') or 'boleto',
        installation_fee=contrato.get('installation_fee'),
        notes='Importado do SGR (Hinova).',
    ))
    db.flush()
    stats.contracts_created += 1


def _import_billings(db: Session, node: ClientNode, client_id: int, stats: ImportStats) -> None:
    """Grava o histórico de cobrança do cliente.

    Cada entrada já vem achatada por linha de discriminação (ver
    _fetch_boletos). O veículo é resolvido pela placa; quando a placa não foi
    migrada (máquina sem placa no padrão), a cobrança entra sem veículo em vez
    de ser descartada — o valor continua fazendo parte do histórico do cliente.

    Dedup por (cliente, nosso_numero, veículo, competência): é o que
    identifica uma linha de cobrança do SGR sem termos external_id.
    """
    for mapped in node.billings:
        if mapped.get('amount') is None or not mapped.get('due_date'):
            stats.billings_skipped += 1
            stats.skip(
                f"cobrança SGR #{mapped.get('external_id')}: sem valor ou sem vencimento"
            )
            continue

        placa = mapped.get('vehicle_plate')
        veiculo = _find_vehicle(db, placa) if placa else None

        existente = (
            db.query(Billing)
            .filter(
                Billing.client_id == client_id,
                Billing.receipt_number == mapped.get('receipt_number'),
                Billing.vehicle_id == (veiculo.id if veiculo else None),
                Billing.period_label == mapped.get('period_label'),
                Billing.is_deleted.is_(False),
            )
            .first()
        )
        if existente:
            stats.billings_reused += 1
            continue

        db.add(Billing(
            client_id=client_id,
            vehicle_id=veiculo.id if veiculo else None,
            title=_trunc(mapped.get('title'), 160),
            billing_type='recorrente',
            amount=mapped['amount'],
            due_date=_to_date(mapped.get('due_date')),
            payment_date=_to_date(mapped.get('payment_date')),
            paid_amount=mapped.get('paid_amount'),
            payment_method=_trunc(mapped.get('payment_method'), 40),
            receipt_number=_trunc(mapped.get('receipt_number'), 40),
            installment_number=mapped.get('installment_number'),
            installment_total=mapped.get('installment_total'),
            period_label=_trunc(mapped.get('period_label'), 20),
            status=BillingStatus(mapped.get('status') or BillingStatus.PENDING.value),
            notes='Importado do SGR (Hinova).',
        ))
        stats.billings_created += 1


def import_poc_result(db: Session, result: PocRunResult, dry_run: bool = True) -> ImportStats:
    """Insere no MasterSat os clientes/veículos/rastreadores já lidos do SGR.

    Em `dry_run` tudo roda dentro da transação e sofre rollback no final —
    serve para ver exatamente o que seria criado, inclusive erros de
    constraint, sem deixar nada no banco.
    """
    stats = ImportStats()
    plano_por_codigo = _import_plans(db, result, stats)

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
                tracker = _find_tracker(db, imei) if imei else None
                if not imei:
                    stats.trackers_skipped += 1
                    stats.skip(
                        f"rastreador do veículo {plate}: sem IMEI (obrigatório no MasterSat)"
                    )
                elif tracker:
                    stats.trackers_reused += 1
                else:
                    tracker = _build_tracker(tnode.mapped, client.id, vehicle.id)
                    db.add(tracker)
                    db.flush()
                    stats.trackers_created += 1

                # O contrato sai do MESMO registro de vínculo que gera o
                # rastreador, e não depende de ele ter entrado: no SGR existe
                # veículo com cobrança ativa cujo equipamento está sem IMEI.
                if tnode.contract:
                    _import_contract(
                        db, tnode.contract, client.id, vehicle.id,
                        tracker.id if tracker else None,
                        plano_por_codigo, stats, plate,
                    )

        # Por último: o histórico de cobrança precisa dos veículos já gravados
        # para resolver a placa de cada linha da discriminação do boleto.
        _import_billings(db, node, client.id, stats)

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return stats
