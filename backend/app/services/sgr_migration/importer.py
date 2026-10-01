"""
Importação dos dados do SGR para o banco do MasterSat.

Diferente do restante do módulo (que é só leitura), este arquivo ESCREVE —
mas só é chamado pelo scripts/sgr_import.py, que exige --apply explícito e
trava em banco que não seja local.

Fase 04 (SGR-01..07) — o que mudou em relação ao importador insert-only:

* IDENTIDADE DE ORIGEM (``sgr_vinculos``). Cliente, veículo, rastreador,
  contrato e plano ficam amarrados ao código do SGR. Chave natural (CPF,
  placa, IMEI) só serve para ADOTAR um registro existente, e só depois de
  conferir o dono: placa/IMEI de outro cliente vira conflito de
  transferência (``sgr_conflitos``), nunca contrato cruzado.
* DOCUMENTO × OBRIGAÇÃO × DESCONTO (``sgr_documentos``/``_linhas``). O boleto
  consolidado é conciliado antes de liberar cobrança: a soma das linhas tem
  de bater com o total da origem, e cada desconto é alocado nas obrigações
  da mesma placa. A cobrança nasce com o valor LÍQUIDO; o bruto e o desconto
  ficam na linha. Documento que não fecha fica bloqueado (pendência
  explícita), nunca importado com valor errado.
* SINCRONIZAÇÃO VERSIONADA. Cada documento guarda o estado da origem aplicado
  por último e a impressão digital da cobrança local. Na rodada seguinte:
  origem igual → nada; origem mudou e o MasterSat não → transição permitida
  é aplicada (aberto→pago, aberto→cancelado, novo vencimento), sempre pela
  política bancária única; MasterSat também mudou, ou transição fora da
  lista (pago→aberto, cancelado→pago, valor alterado) → conflito para decisão.
* CHECKPOINT POR CLIENTE. Cada cliente é uma unidade confirmada numa
  transação curta (``sgr_execucao_unidades``); falha de um não desfaz os
  outros e a retomada pula o que já foi aplicado com o mesmo dado.
* OUTBOX DE ARQUIVOS (``sgr_arquivos``). PDF/XML são pedidos na transação da
  unidade e baixados depois, fora dela, com chave de objeto estável e
  download controlado (download.py). Retomar converge: o arquivo que falhou é
  tentado de novo mesmo com a cobrança já existente.

Em ``dry_run`` o mesmo caminho roda com flush no lugar de commit e tudo sofre
rollback no fim; nenhum arquivo é baixado nem enviado ao storage.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billing import Billing
from app.models.billing_change_log import BillingChangeLog
from app.models.client import Client
from app.models.contract import Contract
from app.models.document import Document
from app.models.enums import BillingStatus, ClientStatus, TrackerStatus, VehicleStatus
from app.models.plan import Plan
from app.models.sgr_migracao import (
    SgrArquivo,
    SgrConflito,
    SgrDocumento,
    SgrDocumentoLinha,
    SgrExecucao,
    SgrExecucaoUnidade,
    SgrVinculo,
)
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.services import titulo_bancario
from app.services.financial import (
    RECURRING_BILLING_TYPES,
    BillingSubstitutionError,
    existing_recurring_periods,
    lock_billings_for_update,
    occupying_recurring_filter,
    release_billing_competencia,
)
from app.services.competencia import competencia_do_rotulo
from app.services.sgr_migration.normalize import is_valid_plate, parse_centavos
from app.services.sgr_migration.poc import ClientNode, PocRunResult

logger = logging.getLogger(__name__)

_NOTA_IMPORTADO = 'Importado do SGR (Hinova).'


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
    # Reemissões canceladas/removidas de uma mensalidade cujo mês já tem o
    # boleto que valeu (ver _criar_cobrancas) — só contagem, sem 1 aviso cada.
    billings_replaced: int = 0
    # Transições aplicadas a cobranças já importadas (aberto→pago etc.).
    billings_updated: int = 0
    invoices_created: int = 0
    invoices_reused: int = 0
    invoices_skipped: int = 0
    boletos_created: int = 0
    boletos_reused: int = 0
    boletos_skipped: int = 0
    documentos_conciliados: int = 0
    documentos_inalterados: int = 0
    documentos_bloqueados: int = 0
    descontos_centavos: int = 0
    linhas_bonificadas: int = 0
    conflitos: int = 0
    clientes_bloqueados: int = 0
    unidades_falharam: int = 0
    unidades_reaproveitadas: int = 0
    arquivos_pendentes: int = 0
    arquivos_bloqueados: int = 0
    bloqueios: dict[str, int] = field(default_factory=dict)
    conflitos_por_tipo: dict[str, int] = field(default_factory=dict)
    hosts_arquivos: dict[str, int] = field(default_factory=dict)
    skips: list[str] = field(default_factory=list)
    execucao_id: int | None = None
    status_execucao: str | None = None
    manifesto: dict = field(default_factory=dict)

    def skip(self, motivo: str) -> None:
        self.skips.append(motivo)

    def contar(self, campo: str, chave: str, n: int = 1) -> None:
        alvo = getattr(self, campo)
        alvo[chave] = alvo.get(chave, 0) + n

    def merge(self, outro: 'ImportStats') -> None:
        for f in fields(self):
            if f.name in ('execucao_id', 'status_execucao', 'manifesto'):
                continue
            valor = getattr(outro, f.name)
            if isinstance(valor, int):
                setattr(self, f.name, getattr(self, f.name) + valor)
            elif isinstance(valor, dict):
                for chave, n in valor.items():
                    self.contar(f.name, chave, n)
            elif isinstance(valor, list):
                getattr(self, f.name).extend(valor)

    def resumo(self) -> dict:
        return {
            f.name: getattr(self, f.name) for f in fields(self)
            if isinstance(getattr(self, f.name), int) and getattr(self, f.name)
            and f.name != 'execucao_id'
        }


class _Bloqueio(Exception):
    """A unidade inteira não pode ser importada (decidido antes de gravar)."""

    def __init__(self, motivo: str):
        self.motivo = motivo
        super().__init__(motivo)


@dataclass
class _Contexto:
    db: Session
    dry_run: bool
    execucao: SgrExecucao | None
    plano_por_codigo: dict[str, Plan] = field(default_factory=dict)
    # Por unidade (cliente): placa → veículo aceito; placas em conflito.
    veiculos: dict[str, Vehicle] = field(default_factory=dict)
    placas_bloqueadas: set[str] = field(default_factory=set)

    @property
    def execucao_id(self) -> int | None:
        return self.execucao.id if self.execucao is not None else None

    def nova_unidade(self) -> None:
        self.veiculos = {}
        self.placas_bloqueadas = set()


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _hash(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False).encode('utf-8'),
    ).hexdigest()


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


def _reais(centavos: int) -> Decimal:
    return (Decimal(centavos) / 100).quantize(Decimal('0.01'))


def _brl(centavos: int) -> str:
    return f'R$ {centavos / 100:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _centavos_do_banco(valor) -> int | None:
    if valor is None:
        return None
    return int((Decimal(str(valor)) * 100).quantize(Decimal('1')))


def _versao_codigo() -> str | None:
    try:
        saida = subprocess.run(
            ['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return saida.stdout.strip()[:64] or None


# ---------------------------------------------------------------------------
# Identidade de origem e conflitos
# ---------------------------------------------------------------------------

def _vinculo(db: Session, entidade: str, chave) -> SgrVinculo | None:
    if chave in (None, ''):
        return None
    return db.scalar(select(SgrVinculo).where(
        SgrVinculo.entidade == entidade, SgrVinculo.chave_origem == str(chave),
    ))


def _vinculo_local(db: Session, entidade: str, local_id: int) -> SgrVinculo | None:
    return db.scalar(select(SgrVinculo).where(
        SgrVinculo.entidade == entidade, SgrVinculo.local_id == local_id,
    ))


def _vincular(ctx: _Contexto, entidade: str, chave, local_id: int, criado_por: str, hash_origem=None) -> None:
    if chave in (None, ''):
        return
    ctx.db.add(SgrVinculo(
        entidade=entidade, chave_origem=str(chave)[:120], local_id=local_id, hash_origem=hash_origem,
        criado_por=criado_por, primeira_execucao_id=ctx.execucao_id, ultima_execucao_id=ctx.execucao_id,
    ))
    ctx.db.flush()


def _tocar(ctx: _Contexto, vinculo: SgrVinculo, hash_origem=None) -> None:
    vinculo.ultima_execucao_id = ctx.execucao_id
    if hash_origem:
        vinculo.hash_origem = hash_origem


def _decisao_manter_local(db: Session, entidade: str, chave: str, hash_origem: str | None) -> bool:
    """Já houve decisão "manter o MasterSat" para este registro neste mesmo
    estado de origem (qualquer que tenha sido o tipo do conflito)."""
    if not hash_origem:
        return False
    return db.scalar(select(SgrConflito.id).where(
        SgrConflito.entidade == entidade, SgrConflito.chave_origem == chave,
        SgrConflito.status == 'resolvido', SgrConflito.resolucao == 'manter_local',
        SgrConflito.hash_origem == hash_origem,
    ).limit(1)) is not None


def _conflito(
    ctx: _Contexto, stats: ImportStats, entidade: str, chave, tipo: str, *,
    tabela: str | None = None, local_id: int | None = None, detalhe: dict | None = None,
    hash_origem: str | None = None,
) -> SgrConflito | None:
    """Abre (ou atualiza) o conflito aberto deste assunto. Devolve None se já
    existe decisão de manter o MasterSat para o mesmo estado da origem."""
    chave = str(chave)[:160]
    if _decisao_manter_local(ctx.db, entidade, chave, hash_origem):
        return None
    existente = ctx.db.scalar(select(SgrConflito).where(
        SgrConflito.entidade == entidade, SgrConflito.chave_origem == chave,
        SgrConflito.tipo == tipo, SgrConflito.status == 'aberto',
    ))
    if existente is None:
        existente = SgrConflito(entidade=entidade, chave_origem=chave, tipo=tipo, status='aberto')
        ctx.db.add(existente)
    existente.execucao_id = ctx.execucao_id
    existente.tabela_local = tabela
    existente.local_id = local_id
    existente.detalhe = detalhe or {}
    existente.hash_origem = hash_origem
    ctx.db.flush()
    stats.conflitos += 1
    stats.contar('conflitos_por_tipo', tipo)
    return existente


# ---------------------------------------------------------------------------
# Cadastro: cliente, plano, veículo, rastreador, contrato
# ---------------------------------------------------------------------------

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
        notes=_NOTA_IMPORTADO,
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
        notes=_NOTA_IMPORTADO,
    )


def _resolver_cliente(ctx: _Contexto, stats: ImportStats, node: ClientNode) -> Client | None:
    """Cliente da unidade. None = ignorado (falta dado obrigatório).
    Levanta _Bloqueio quando a identidade é ambígua — antes de gravar nada."""
    db = ctx.db
    mapped = node.mapped
    cpf_cnpj = mapped.get('cpf_cnpj')
    codigo = mapped.get('external_id')
    if not cpf_cnpj or not mapped.get('name'):
        stats.clients_skipped += 1
        stats.skip(f"cliente SGR #{codigo}: sem nome ou sem CPF/CNPJ — obrigatórios no MasterSat")
        return None

    vinculo = _vinculo(db, 'cliente', codigo)
    if vinculo is not None:
        client = db.get(Client, vinculo.local_id)
        if client is None or client.is_deleted:
            _conflito(ctx, stats, 'cliente', codigo, 'cliente_removido_localmente',
                      tabela='clients', local_id=vinculo.local_id)
            raise _Bloqueio('cliente_removido_localmente')
        if client.cpf_cnpj != cpf_cnpj:
            # A identidade é o código; o documento divergente é informado,
            # não sobrescrito (pode ser correção feita à mão).
            _conflito(ctx, stats, 'cliente', codigo, 'documento_divergente',
                      tabela='clients', local_id=client.id, hash_origem=_hash(cpf_cnpj),
                      detalhe={'observacao': 'CPF/CNPJ da origem difere do cadastro local'})
        _tocar(ctx, vinculo)
        stats.clients_reused += 1
        return client

    existente = _find_client(db, cpf_cnpj)
    if existente is not None:
        if codigo:
            outro = _vinculo_local(db, 'cliente', existente.id)
            if outro is not None and outro.chave_origem != str(codigo):
                _conflito(ctx, stats, 'cliente', codigo, 'documento_em_outro_cliente_sgr',
                          tabela='clients', local_id=existente.id,
                          detalhe={'cod_cliente_ja_vinculado': outro.chave_origem})
                raise _Bloqueio('documento_em_outro_cliente_sgr')
            if outro is None:
                _vincular(ctx, 'cliente', codigo, existente.id, 'adocao')
        stats.clients_reused += 1
        return existente

    client = _build_client(mapped)
    db.add(client)
    db.flush()  # precisa do id para os veículos
    _vincular(ctx, 'cliente', codigo, client.id, 'importacao')
    stats.clients_created += 1
    return client


def _import_plans(ctx: _Contexto, stats: ImportStats, result: PocRunResult) -> dict[str, Plan]:
    """Cria os Planos vindos de /get_grupo_mensalidade e /get_grupo_adesao.

    Identidade = código do grupo. Sem vínculo, a chave natural é o NOME do
    plano (coluna unique em plans). O retorno é um índice código → Plan, que é
    como o vínculo referencia o plano (`cod_grupo_vinculo`).
    """
    db = ctx.db
    por_codigo: dict[str, Plan] = {}
    for mapped in result.plans:
        nome = mapped.get('name')
        codigo = str(mapped.get('external_id') or '')
        if not nome or mapped.get('price') is None:
            stats.plans_skipped += 1
            stats.skip(f'plano SGR #{codigo}: sem nome ou sem valor — obrigatórios no MasterSat')
            continue

        vinculo = _vinculo(db, 'plano', codigo)
        existente = db.get(Plan, vinculo.local_id) if vinculo is not None else None
        if existente is not None and not existente.is_deleted:
            _tocar(ctx, vinculo)
            stats.plans_reused += 1
            por_codigo[codigo] = existente
            continue

        existente = db.query(Plan).filter(Plan.name == nome, Plan.is_deleted.is_(False)).first()
        if existente:
            if vinculo is None:
                _vincular(ctx, 'plano', codigo, existente.id, 'adocao')
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
        if vinculo is None:
            _vincular(ctx, 'plano', codigo, plano.id, 'importacao')
        stats.plans_created += 1
        por_codigo[codigo] = plano
    return por_codigo


def _resolver_veiculo(ctx: _Contexto, stats: ImportStats, client: Client, vnode) -> Vehicle | None:
    """Veículo da unidade, com dono conferido. Placa de veículo que pertence a
    OUTRO cliente no MasterSat vira conflito de transferência e fica bloqueada
    nesta unidade (nem contrato, nem cobrança ligada a ela)."""
    db = ctx.db
    mapped = vnode.mapped
    plate = mapped.get('plate')
    codigo = mapped.get('external_id')
    if not plate:
        stats.vehicles_skipped += 1
        stats.skip(f"veículo SGR #{codigo}: sem placa")
        return None
    if not is_valid_plate(plate):
        # O MasterSat exige placa no padrão do schema — inclusive ao
        # SERIALIZAR a resposta. Um registro assim não só é recusado no
        # cadastro: ele derruba o GET /vehicles inteiro.
        stats.vehicles_skipped += 1
        stats.skip(
            f"veículo SGR #{codigo}: placa '{plate}' fora do padrão "
            f"(máquina sem placa ou erro de cadastro) — o modelo do MasterSat não comporta"
        )
        return None

    def _transferencia(vehicle: Vehicle, via: str) -> None:
        _conflito(
            ctx, stats, 'veiculo', codigo or f'placa:{plate}', 'transferencia_veiculo',
            tabela='vehicles', local_id=vehicle.id,
            detalhe={'dono_local_client_id': vehicle.client_id, 'dono_origem_client_id': client.id,
                     'cod_cliente_origem': client_codigo, 'cod_veiculo_origem': codigo, 'identificado_por': via},
        )
        ctx.placas_bloqueadas.add(plate)
        stats.vehicles_skipped += 1
        stats.skip(
            f'veículo {plate}: pertence a outro cliente no MasterSat — conflito de transferência '
            f'aberto; contrato e cobranças desta placa aguardam a decisão'
        )

    client_codigo = None
    vinc_cliente = _vinculo_local(db, 'cliente', client.id)
    if vinc_cliente is not None:
        client_codigo = vinc_cliente.chave_origem

    vinculo = _vinculo(db, 'veiculo', codigo)
    if vinculo is not None:
        vehicle = db.get(Vehicle, vinculo.local_id)
        if vehicle is None or vehicle.is_deleted:
            _conflito(ctx, stats, 'veiculo', codigo, 'veiculo_removido_localmente',
                      tabela='vehicles', local_id=vinculo.local_id)
            ctx.placas_bloqueadas.add(plate)
            stats.vehicles_skipped += 1
            return None
        if vehicle.client_id != client.id:
            _transferencia(vehicle, 'codigo_origem')
            return None
        if vehicle.plate != plate:
            _conflito(ctx, stats, 'veiculo', codigo, 'placa_divergente', tabela='vehicles',
                      local_id=vehicle.id, hash_origem=_hash(plate),
                      detalhe={'observacao': 'placa da origem difere do cadastro local'})
        _tocar(ctx, vinculo)
        stats.vehicles_reused += 1
        ctx.veiculos[plate] = vehicle
        return vehicle

    vehicle = _find_vehicle(db, plate)
    if vehicle is not None:
        if vehicle.client_id != client.id:
            _transferencia(vehicle, 'placa')
            return None
        if codigo:
            outro = _vinculo_local(db, 'veiculo', vehicle.id)
            if outro is not None and outro.chave_origem != str(codigo):
                _conflito(ctx, stats, 'veiculo', codigo, 'placa_em_outro_veiculo_sgr', tabela='vehicles',
                          local_id=vehicle.id, detalhe={'cod_veiculo_ja_vinculado': outro.chave_origem})
                ctx.placas_bloqueadas.add(plate)
                stats.vehicles_skipped += 1
                return None
            if outro is None:
                _vincular(ctx, 'veiculo', codigo, vehicle.id, 'adocao')
        stats.vehicles_reused += 1
        ctx.veiculos[plate] = vehicle
        return vehicle

    vehicle = _build_vehicle(mapped, client.id)
    db.add(vehicle)
    db.flush()
    _vincular(ctx, 'veiculo', codigo, vehicle.id, 'importacao')
    stats.vehicles_created += 1
    ctx.veiculos[plate] = vehicle
    return vehicle


def _resolver_rastreador(
    ctx: _Contexto, stats: ImportStats, client: Client, vehicle: Vehicle, tnode, plate: str,
) -> Tracker | None:
    db = ctx.db
    mapped = tnode.mapped
    imei = mapped.get('imei')
    codigo = mapped.get('external_id')
    if not imei:
        stats.trackers_skipped += 1
        stats.skip(f"rastreador do veículo {plate}: sem IMEI (obrigatório no MasterSat)")
        return None

    vinculo = _vinculo(db, 'rastreador', codigo)
    tracker = db.get(Tracker, vinculo.local_id) if vinculo is not None else None
    if tracker is not None and tracker.is_deleted:
        tracker = None
    if tracker is None:
        tracker = _find_tracker(db, imei)

    if tracker is not None:
        outro_veiculo = tracker.vehicle_id not in (None, vehicle.id)
        outro_cliente = tracker.client_id not in (None, client.id)
        if outro_veiculo or outro_cliente:
            _conflito(
                ctx, stats, 'rastreador', codigo or f'imei:{imei[-4:]}', 'rastreador_em_outro_veiculo',
                tabela='trackers', local_id=tracker.id,
                detalhe={'vehicle_id_local': tracker.vehicle_id, 'client_id_local': tracker.client_id,
                         'vehicle_id_origem': vehicle.id, 'client_id_origem': client.id},
            )
            stats.trackers_skipped += 1
            stats.skip(
                f'rastreador do veículo {plate}: IMEI já instalado em outro veículo/cliente no '
                f'MasterSat — não reaproveitado (conflito aberto)'
            )
            return None
        if tracker.imei != imei:
            _conflito(ctx, stats, 'rastreador', codigo, 'imei_divergente', tabela='trackers',
                      local_id=tracker.id, hash_origem=_hash(imei),
                      detalhe={'observacao': 'IMEI da origem difere do cadastro local'})
        if vinculo is None and codigo:
            outro = _vinculo_local(db, 'rastreador', tracker.id)
            if outro is None:
                _vincular(ctx, 'rastreador', codigo, tracker.id, 'adocao')
        elif vinculo is not None:
            _tocar(ctx, vinculo)
        stats.trackers_reused += 1
        return tracker

    tracker = _build_tracker(mapped, client.id, vehicle.id)
    db.add(tracker)
    db.flush()
    if vinculo is None:
        _vincular(ctx, 'rastreador', codigo, tracker.id, 'importacao')
    stats.trackers_created += 1
    return tracker


def _find_contract(db: Session, client_id: int, vehicle_id: int) -> Contract | None:
    """Contrato já existente de (cliente, veículo) — chave de adoção.

    No SGR a cobrança é configurada por vínculo, e um veículo ativo tem um
    vínculo vigente. Se existir mais de um vínculo por veículo (renovação
    registrada como novo vínculo), o primeiro é reaproveitado em vez de
    duplicar — que é o comportamento seguro para um importador.
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


def _completar_rastreador(db: Session, contrato: Contract, tracker_id: int | None) -> None:
    """Rodada anterior criou o contrato quando o equipamento vinha sem IMEI;
    agora que o rastreador existe, liga (senão a tela mostra "Sem plano").
    Só preenche o vazio: contrato já ligado a outro rastreador não é trocado."""
    if contrato.tracker_id is None and tracker_id is not None:
        contrato.tracker_id = tracker_id
        db.flush()


def _import_contract(
    ctx: _Contexto, stats: ImportStats, contrato: dict, client: Client, vehicle: Vehicle,
    tracker_id: int | None, placa: str,
) -> None:
    db = ctx.db
    plan_code = contrato.get('plan_external_id')
    plano = ctx.plano_por_codigo.get(str(plan_code)) if plan_code else None
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

    codigo = contrato.get('external_id')
    vinculo = _vinculo(db, 'contrato', codigo)
    if vinculo is not None:
        existente = db.get(Contract, vinculo.local_id)
        if existente is not None and not existente.is_deleted:
            if existente.client_id != client.id or existente.vehicle_id != vehicle.id:
                _conflito(ctx, stats, 'contrato', codigo, 'contrato_divergente', tabela='contracts',
                          local_id=existente.id,
                          detalhe={'client_id_local': existente.client_id, 'vehicle_id_local': existente.vehicle_id,
                                   'client_id_origem': client.id, 'vehicle_id_origem': vehicle.id})
            else:
                _completar_rastreador(db, existente, tracker_id)
            _tocar(ctx, vinculo)
            stats.contracts_reused += 1
            return

    existente = _find_contract(db, client.id, vehicle.id)
    if existente is not None:
        if vinculo is None and codigo and _vinculo_local(db, 'contrato', existente.id) is None:
            _vincular(ctx, 'contrato', codigo, existente.id, 'adocao')
        _completar_rastreador(db, existente, tracker_id)
        stats.contracts_reused += 1
        return

    interveniente_id = None
    cpf_interveniente = contrato.get('interveniente_cpf')
    if cpf_interveniente:
        outro = _find_client(db, cpf_interveniente)
        # Interveniente igual ao próprio cliente é o caso normal no SGR e no
        # MasterSat significa "sem interveniente" (a coluna fica nula).
        if outro and outro.id != client.id:
            interveniente_id = outro.id

    novo = Contract(
        client_id=client.id,
        vehicle_id=vehicle.id,
        tracker_id=tracker_id,
        plan_id=plano.id,
        interveniente_client_id=interveniente_id,
        start_date=_to_date(contrato.get('start_date')),
        billing_day=contrato.get('billing_day'),
        status=contrato.get('status') or 'ativo',
        billing_modality=contrato.get('billing_modality') or 'boleto',
        installation_fee=contrato.get('installation_fee'),
        notes=_NOTA_IMPORTADO,
    )
    db.add(novo)
    db.flush()
    if vinculo is None:
        _vincular(ctx, 'contrato', codigo, novo.id, 'importacao')
    stats.contracts_created += 1


# ---------------------------------------------------------------------------
# Documentos: conciliação, desconto e cobranças
# ---------------------------------------------------------------------------

_RANK_STATUS = {
    BillingStatus.PAID.value: 0,
    BillingStatus.PENDING.value: 1,
    BillingStatus.OVERDUE.value: 1,
}
# Em que obrigação um desconto do mesmo documento/placa é abatido primeiro.
_PRIORIDADE_TIPO = {'recorrente': 0, 'prorata': 1, 'primeira_mensalidade': 2, 'carne': 3}


def _prioridade_no_mes(mapped: dict) -> tuple:
    try:
        cod = -int((mapped.get('sgr_payload') or {}).get('cod_boleto') or mapped.get('external_id') or 0)
    except (TypeError, ValueError):
        cod = 0
    return (_RANK_STATUS.get(mapped.get('status') or '', 2), cod)


def _cod_documento(mapped: dict) -> str | None:
    cod = (mapped.get('sgr_payload') or {}).get('cod_boleto') or mapped.get('external_id')
    return str(cod) if cod not in (None, '') else None


@dataclass
class LinhaOrigem:
    chave: str
    indice: int
    mapped: dict
    placa: str | None
    produto: str | None
    mes: str | None
    centavos: int | None
    billing_type: str

    @property
    def rotulo(self) -> str:
        return f"{self.placa or 'sem placa'} {self.mes or ''}".strip()


_SEM_ESPACO = re.compile(r'\s+')


def linhas_do_documento(cod: str, linhas: list[dict]) -> list[LinhaOrigem]:
    """Chave estável de cada linha: código do documento, placa, produto e
    mês, mais a ocorrência (linhas idênticas nesses campos). Não usa o valor:
    valor alterado na origem é mudança da MESMA linha."""
    ocorrencias: dict[tuple, int] = defaultdict(int)
    saida: list[LinhaOrigem] = []
    for i, mapped in enumerate(linhas):
        placa = mapped.get('vehicle_plate') or None
        produto = _SEM_ESPACO.sub(' ', str(mapped.get('produto') or '').strip().upper())[:60] or None
        mes = mapped.get('period_label') or None
        base = (placa, produto, mes)
        ocorrencias[base] += 1
        chave = f"{cod}:{placa or '-'}:{produto or '-'}:{mes or '-'}:{ocorrencias[base]}"
        if 'amount_cents' in mapped:
            centavos = mapped.get('amount_cents')
        else:  # entrada montada à mão (testes antigos): valor em float
            centavos = parse_centavos(mapped.get('amount'))
        saida.append(LinhaOrigem(
            chave=chave[:160], indice=i, mapped=mapped, placa=placa, produto=produto, mes=mes,
            centavos=centavos, billing_type=mapped.get('billing_type') or 'recorrente',
        ))
    return saida


def _total_do_documento(base: dict) -> int | None:
    if 'document_total_cents' in base:
        return base.get('document_total_cents')
    return parse_centavos((base.get('sgr_payload') or {}).get('valor'))


@dataclass
class PlanoConciliacao:
    liquido: dict[str, int]                       # chave da obrigação → centavos líquidos
    alocacoes: dict[str, list[tuple[str, int]]]   # chave do desconto → [(obrigação, centavos)]
    ignoradas: list[LinhaOrigem]                  # linhas sem valor que não entram na soma


def conciliar(linhas: list[LinhaOrigem], total: int | None) -> tuple[PlanoConciliacao | None, str | None, dict]:
    """Confere o documento e aloca os descontos. Devolve (plano, None, {}) ou
    (None, motivo_do_bloqueio, detalhe).

    Regra de alocação (decisão D1 em docs/migracao-sgr/politica-conflitos.md):
    o desconto abate obrigações da MESMA placa, primeiro as do mesmo mês,
    depois mensalidade > pró-rata > 1ª mensalidade > carnê > demais, na ordem
    do documento. Desconto sem placa só é alocado se o documento tiver uma
    única placa. O que não couber bloqueia o documento.
    """
    if total is None:
        return None, 'total_origem_ausente', {}
    validas = [linha for linha in linhas if linha.centavos is not None]
    sem_valor = [linha for linha in linhas if linha.centavos is None]
    soma = sum(linha.centavos for linha in validas)
    if sem_valor and soma != total:
        return None, 'valor_malformado', {'linhas_sem_valor': len(sem_valor)}
    if soma != total:
        return None, 'soma_linhas_diverge', {'total_origem_centavos': total, 'soma_linhas_centavos': soma}
    if total < 0:
        return None, 'total_negativo', {'total_origem_centavos': total}

    positivas = [linha for linha in validas if linha.centavos > 0]
    descontos = [linha for linha in validas if linha.centavos < 0]
    restante = {linha.chave: linha.centavos for linha in positivas}
    alocacoes: dict[str, list[tuple[str, int]]] = {}
    placas = {linha.placa for linha in positivas}
    for desconto in descontos:
        if desconto.placa:
            alvo = [p for p in positivas if p.placa == desconto.placa]
        elif len(placas) == 1:
            alvo = list(positivas)
        else:
            return None, 'desconto_sem_placa_ambiguo', {'linha': desconto.chave}
        if not alvo:
            return None, 'desconto_sem_obrigacao_da_placa', {'linha': desconto.chave}
        alvo.sort(key=lambda p: (
            bool(desconto.mes) and p.mes != desconto.mes,
            _PRIORIDADE_TIPO.get(p.billing_type, 9),
            p.indice,
        ))
        falta = -desconto.centavos
        partes: list[tuple[str, int]] = []
        for obrigacao in alvo:
            usar = min(falta, restante[obrigacao.chave])
            if usar:
                restante[obrigacao.chave] -= usar
                partes.append((obrigacao.chave, usar))
                falta -= usar
            if not falta:
                break
        if falta:
            return None, 'desconto_maior_que_obrigacoes', {'linha': desconto.chave, 'sobra_centavos': falta}
        alocacoes[desconto.chave] = partes
    return PlanoConciliacao(liquido=restante, alocacoes=alocacoes, ignoradas=sem_valor), None, {}


def _grupo(status) -> str:
    valor = status.value if isinstance(status, BillingStatus) else str(status or '')
    if valor in (BillingStatus.PENDING.value, BillingStatus.OVERDUE.value):
        return 'aberto'
    if valor == BillingStatus.PAID.value:
        return 'pago'
    return 'cancelado'


def impressao_local(billing: Billing) -> str:
    """Estado da cobrança que só a própria importação deveria mudar.

    PENDENTE e VENCIDA contam como o mesmo "aberto": o worker reclassifica
    por data, e isso não é edição humana.
    """
    return _hash({
        'grupo': _grupo(billing.status),
        'valor': _centavos_do_banco(billing.amount),
        'vencimento': billing.due_date.isoformat() if billing.due_date else None,
        'pago': _centavos_do_banco(billing.paid_amount),
        'pagamento': billing.payment_date.isoformat() if billing.payment_date else None,
        'removida': bool(billing.is_deleted),
        'liberada': bool(billing.competencia_liberada),
        'substituida_por': billing.substituted_by_id,
    })


def _status_alvo(mapped: dict) -> BillingStatus:
    # 'ABERTO' no SGR não diz se já venceu — quem classifica isso no
    # MasterSat é o status VENCIDA, que é o que as telas de pendência e
    # inadimplência filtram. Sem esta conversão, cobrança vencida importada
    # fica invisível nessas telas.
    status = BillingStatus(mapped.get('status') or BillingStatus.PENDING.value)
    vencimento = _to_date(mapped.get('due_date'))
    if status == BillingStatus.PENDING and vencimento and vencimento < date.today():
        status = BillingStatus.OVERDUE
    return status


def _pago_centavos(mapped: dict) -> int | None:
    if 'paid_amount_cents' in mapped:
        valor = mapped.get('paid_amount_cents')
    else:
        valor = parse_centavos(mapped.get('paid_amount'))
    # O SGR manda valor_pagamento '0,00' em boleto não pago; no MasterSat isso
    # é ausência de pagamento (paid_amount > 0 é regra do schema e do banco).
    return valor if valor and valor > 0 else None


def _snapshot_documento(base: dict, linhas: list[LinhaOrigem], total: int | None) -> dict:
    return {
        'status': base.get('status'),
        'total': total,
        'vencimento': base.get('due_date'),
        'pagamento': base.get('payment_date'),
        'pago': _pago_centavos(base),
        'forma': base.get('payment_method'),
        'linhas': [[linha.chave, linha.centavos] for linha in linhas],
    }


_NFSE_CATEGORIA = 'nota_fiscal'
_BOLETO_CATEGORIA = 'boleto'


def _nota_cobranca(mapped: dict) -> str:
    """Observação da cobrança, com a linha digitável quando o boleto está aberto.

    A linha digitável e o código de barras não têm coluna própria em
    `billings` (no MasterSat isso vive em ailos_boletos, que é da emissão
    pelo Ailos e não comporta boleto de outra origem). Guardar na observação
    mantém o dado acessível sem forçar um modelo que não é dele.
    """
    partes = [_NOTA_IMPORTADO]
    if mapped.get('linha_digitavel'):
        partes.append(f"Linha digitável (SGR): {mapped['linha_digitavel']}")
    if mapped.get('cod_barras'):
        partes.append(f"Código de barras (SGR): {mapped['cod_barras']}")
    if mapped.get('pix_copia_cola'):
        partes.append(f"PIX copia e cola (SGR): {mapped['pix_copia_cola']}")
    if len(partes) > 1:
        partes.append(
            'ATENÇÃO: boleto emitido pelo SGR e ainda em aberto lá — não reenviar '
            'sem confirmar que a cobrança não foi reemitida por aqui.'
        )
    return '\n'.join(partes)


@dataclass
class _Destino:
    """Para quem vai a cobrança de uma linha: cliente atendido, pagador,
    veículo e contrato. ``motivo`` preenchido = linha bloqueia o documento."""
    client_id: int
    payer_client_id: int | None = None
    vehicle: Vehicle | None = None
    contrato: Contract | None = None
    motivo: str | None = None


def _destino_da_linha(ctx: _Contexto, client: Client, placa: str | None) -> _Destino:
    if not placa:
        return _Destino(client_id=client.id)
    if placa in ctx.placas_bloqueadas:
        return _Destino(client_id=client.id, motivo='veiculo_em_conflito')
    vehicle = ctx.veiculos.get(placa) or _find_vehicle(ctx.db, placa)
    if vehicle is None:
        # Máquina sem placa no padrão ou veículo fora do escopo que nunca foi
        # migrado: a cobrança entra no cliente, sem veículo — o valor continua
        # fazendo parte do histórico.
        return _Destino(client_id=client.id)
    if vehicle.client_id == client.id:
        return _Destino(client_id=client.id, vehicle=vehicle,
                        contrato=_find_contract(ctx.db, client.id, vehicle.id))
    # Veículo de outro cliente: só é legítimo se este cliente for o
    # responsável financeiro (interveniente) do contrato daquele veículo —
    # aí a cobrança é do cliente atendido, com este como pagador.
    contrato = ctx.db.scalar(select(Contract).where(
        Contract.vehicle_id == vehicle.id, Contract.client_id == vehicle.client_id,
        Contract.interveniente_client_id == client.id, Contract.is_deleted.is_(False),
    ).limit(1))
    if contrato is not None:
        return _Destino(client_id=vehicle.client_id, payer_client_id=client.id, vehicle=vehicle, contrato=contrato)
    return _Destino(client_id=client.id, motivo='placa_de_outro_cliente')


def _liberar_reemissao(ctx: _Contexto, contrato: Contract, periodo: str) -> bool:
    """O mês está ocupado só por mensalidade SGR que a própria origem cancelou
    (reemissão): libera a competência para o boleto que a substituiu.

    Nunca libera cobrança local, paga, aberta ou com título ativo no banco —
    release_billing_competencia aplica a política bancária.
    """
    competencia = competencia_do_rotulo(periodo)
    if competencia is None:
        return False
    ocupantes = ctx.db.scalars(select(Billing).where(
        Billing.contract_id == contrato.id, Billing.competencia == competencia, *occupying_recurring_filter(),
    )).all()
    if not ocupantes:
        return False
    for billing in ocupantes:
        if billing.status != BillingStatus.CANCELED:
            return False
        linha = ctx.db.scalar(select(SgrDocumentoLinha).where(SgrDocumentoLinha.billing_id == billing.id))
        if linha is None:
            return False
        documento = ctx.db.get(SgrDocumento, linha.documento_id)
        if documento is None or _grupo((documento.snapshot_origem or {}).get('status')) != 'cancelado':
            return False
    try:
        for billing in ocupantes:
            release_billing_competencia(
                ctx.db, billing, user_id=None,
                justification='Migração SGR: boleto cancelado na origem e reemitido para o mesmo mês',
            )
    except (titulo_bancario.PoliticaBancariaError, BillingSubstitutionError):
        return False
    ctx.db.flush()
    return True


def _registrar_documento(
    ctx: _Contexto, documento: SgrDocumento | None, cod: str, client: Client, base: dict,
    total: int | None, snapshot: dict, hash_origem: str, status: str, motivo: str | None = None,
    detalhe: dict | None = None, descontos: int = 0,
) -> SgrDocumento:
    if documento is None:
        documento = SgrDocumento(cod_boleto=cod, criado_por='importacao')
        ctx.db.add(documento)
    documento.client_id = client.id
    documento.cod_cliente = str(base.get('client_external_id')) if base.get('client_external_id') else documento.cod_cliente
    documento.status = status
    documento.motivo_bloqueio = motivo
    documento.detalhe_bloqueio = detalhe
    documento.situacao_origem = _trunc((base.get('sgr_payload') or {}).get('situacao'), 40)
    documento.total_origem_centavos = total
    documento.descontos_centavos = descontos
    documento.hash_origem = hash_origem
    documento.snapshot_origem = snapshot
    documento.ultima_execucao_id = ctx.execucao_id
    ctx.db.flush()
    return documento


def _bloquear_documento(
    ctx: _Contexto, stats: ImportStats, documento, cod, client, base, total, snapshot, hash_origem,
    motivo: str, detalhe: dict, n_linhas: int,
) -> None:
    _registrar_documento(ctx, documento, cod, client, base, total, snapshot, hash_origem,
                         'bloqueado', motivo, detalhe)
    stats.documentos_bloqueados += 1
    stats.contar('bloqueios', motivo)
    stats.billings_skipped += n_linhas
    stats.skip(f'documento SGR {cod}: bloqueado ({motivo}) — nenhuma cobrança criada; ver relatório de conciliação')


def _legado(ctx: _Contexto, client: Client, cod: str, base: dict) -> list[Billing]:
    """Cobranças gravadas pelo importador antigo para este documento."""
    candidatos = ctx.db.scalars(select(Billing).where(
        Billing.client_id == client.id, Billing.is_deleted.is_(False),
        Billing.receipt_number == _trunc(base.get('receipt_number'), 40),
        Billing.sgr_payload.is_not(None),
    )).all() if base.get('receipt_number') else []
    saida = []
    for billing in candidatos:
        payload = billing.sgr_payload or {}
        if str(payload.get('cod_boleto') or '') != cod:
            continue
        ligada = ctx.db.scalar(select(SgrDocumentoLinha.id).where(SgrDocumentoLinha.billing_id == billing.id))
        if ligada is None:
            saida.append(billing)
    return saida


def adotar_legado(
    ctx: _Contexto, stats: ImportStats, client: Client, cod: str, base: dict,
    linhas: list[LinhaOrigem], total: int | None, snapshot: dict, hash_origem: str,
    legados: list[Billing], *, criado_por: str = 'importacao', conferir_status: bool = True,
    descontos_compensados: bool = False,
) -> str | None:
    """Liga as cobranças do importador antigo às linhas do documento.

    Sem inventar: cada obrigação tem de casar com exatamente uma cobrança
    pelo título/competência/valor que o importador antigo gravava. Se o
    documento tinha desconto (que o antigo descartava, SGR-01), ou se sobra/
    falta cobrança, o documento fica bloqueado para a compensação revisável
    (scripts/sgr_backfill.py). Devolve o motivo do bloqueio ou None.

    `descontos_compensados`: o backfill já levou cada cobrança ao valor
    líquido (com histórico); então o casamento é pelo líquido e as linhas de
    desconto são registradas com a alocação.
    `conferir_status`: comparar a situação local com a da origem (só faz
    sentido quando a origem é a leitura de agora, não o payload antigo).
    """
    documento = ctx.db.scalar(select(SgrDocumento).where(SgrDocumento.cod_boleto == cod))
    plano, motivo, detalhe = conciliar(linhas, total)
    descontos = [linha for linha in linhas if (linha.centavos or 0) < 0]
    if motivo is None and descontos and not descontos_compensados:
        motivo = 'legado_com_desconto_descartado'
        detalhe = {'descontos_centavos': -sum(d.centavos for d in descontos),
                   'billing_ids': sorted(b.id for b in legados)}
    livres = list(legados)
    pares: list[tuple[LinhaOrigem, Billing]] = []
    sem_cobranca: list[str] = []
    if motivo is None:
        for linha in linhas:
            if (linha.centavos or 0) <= 0:
                continue
            titulo = _trunc(linha.mapped.get('title'), 160)
            esperado = plano.liquido[linha.chave] if descontos_compensados else linha.centavos
            achou = [
                b for b in livres
                if b.title == titulo and (b.period_label or None) == (_trunc(linha.mapped.get('period_label'), 20))
                and _centavos_do_banco(b.amount) == esperado
            ]
            if not achou:
                sem_cobranca.append(linha.chave)
                continue
            livres.remove(achou[0])
            pares.append((linha, achou[0]))
        if livres:
            motivo = 'legado_ambiguo'
            detalhe = {'billing_ids_sem_linha': sorted(b.id for b in livres)}
        elif sem_cobranca and _grupo(base.get('status')) != 'cancelado':
            # Só reemissão cancelada podia ficar sem cobrança no antigo; linha
            # paga/aberta sem cobrança é dinheiro faltando — não se esconde.
            motivo = 'legado_incompleto'
            detalhe = {'linhas_sem_cobranca': sem_cobranca}
    if motivo is not None:
        documento = _registrar_documento(ctx, documento, cod, client, base, total, snapshot, hash_origem,
                                         'bloqueado', motivo, detalhe)
        documento.criado_por = criado_por
        return motivo

    descontos_total = -sum(d.centavos for d in descontos)
    documento = _registrar_documento(ctx, documento, cod, client, base, total, snapshot, hash_origem,
                                     'conciliado', descontos=descontos_total)
    documento.criado_por = criado_por
    por_linha = {linha.chave: billing for linha, billing in pares}
    alocado: dict[str, int] = defaultdict(int)
    for partes in plano.alocacoes.values():
        for chave, centavos in partes:
            alocado[chave] += centavos
    for linha in linhas:
        if linha.centavos is None or linha.centavos == 0:
            continue
        if linha.centavos < 0:
            ctx.db.add(SgrDocumentoLinha(
                documento_id=documento.id, chave_linha=linha.chave, placa=linha.placa, produto=linha.produto,
                mes_referente=linha.mes, tipo='desconto', valor_origem_centavos=linha.centavos,
                alocacao=[{'linha': chave, 'centavos': c} for chave, c in plano.alocacoes.get(linha.chave, [])],
            ))
            continue
        billing = por_linha.get(linha.chave)
        ctx.db.add(SgrDocumentoLinha(
            documento_id=documento.id, chave_linha=linha.chave, placa=linha.placa, produto=linha.produto,
            mes_referente=linha.mes, tipo='obrigacao' if billing is not None else 'descartada',
            valor_origem_centavos=linha.centavos, desconto_alocado_centavos=alocado.get(linha.chave, 0),
            billing_id=billing.id if billing is not None else None,
            hash_local=impressao_local(billing) if billing is not None else None,
        ))
    ctx.db.flush()
    stats.billings_reused += len(pares)
    if not conferir_status:
        return None
    # O antigo não guardava o estado aplicado: se o local já difere da origem
    # não dá para saber quem mudou — vira conflito, nunca sobrescrita.
    for linha, billing in pares:
        if _grupo(billing.status) != _grupo(_status_alvo(linha.mapped)):
            _conflito(ctx, stats, 'documento', linha.chave, 'divergencia_legado', tabela='billings',
                      local_id=billing.id, hash_origem=hash_origem,
                      detalhe={'status_local': _grupo(billing.status),
                               'status_origem': _grupo(_status_alvo(linha.mapped)),
                               'alvo': _alvo_da_linha(linha.mapped)})
    return None


def _alvo_da_linha(mapped: dict) -> dict:
    """Estado que a origem pede para a cobrança (status/datas/pagamento)."""
    return {
        'status': _status_alvo(mapped).value,
        'vencimento': mapped.get('due_date'),
        'pagamento': mapped.get('payment_date'),
        'pago_centavos': _pago_centavos(mapped),
        'forma': _trunc(mapped.get('payment_method'), 40),
    }


def _criar_cobrancas(
    ctx: _Contexto, stats: ImportStats, client: Client, cod: str, base: dict,
    linhas: list[LinhaOrigem], plano: PlanoConciliacao, destinos: dict[str, _Destino],
    total: int, snapshot: dict, hash_origem: str, documento: SgrDocumento | None,
) -> None:
    db = ctx.db
    descontos_total = -sum(linha.centavos for linha in linhas if (linha.centavos or 0) < 0)
    documento = _registrar_documento(ctx, documento, cod, client, base, total, snapshot, hash_origem,
                                     'conciliado', descontos=descontos_total)
    alocado_por_obrigacao: dict[str, int] = defaultdict(int)
    for partes in plano.alocacoes.values():
        for chave, centavos in partes:
            alocado_por_obrigacao[chave] += centavos

    for linha in linhas:
        if linha.centavos is None:
            stats.skip(f'documento SGR {cod}: linha sem valor ignorada (a soma do documento fecha sem ela)')
            continue
        if linha.centavos < 0:
            db.add(SgrDocumentoLinha(
                documento_id=documento.id, chave_linha=linha.chave, placa=linha.placa, produto=linha.produto,
                mes_referente=linha.mes, tipo='desconto', valor_origem_centavos=linha.centavos,
                alocacao=[{'linha': chave, 'centavos': c} for chave, c in plano.alocacoes.get(linha.chave, [])],
            ))
            stats.descontos_centavos += -linha.centavos
            continue
        if linha.centavos == 0:
            continue

        mapped = linha.mapped
        destino = destinos[linha.chave]
        liquido = plano.liquido[linha.chave]
        desconto = alocado_por_obrigacao.get(linha.chave, 0)
        status = _status_alvo(mapped)
        billing_type = linha.billing_type
        notas = _nota_cobranca(mapped)
        if desconto:
            notas += (f'\nDesconto do boleto SGR {cod}: {_brl(desconto)} sobre {_brl(linha.centavos)} '
                      f'(valor cobrado {_brl(liquido)}).')
        tipo_linha = 'obrigacao'
        valor = liquido
        if liquido == 0:
            # Bonificação integral: a obrigação existe (o mês não pode ser
            # cobrado de novo pelo fechamento), mas nada é devido. Cancelada
            # ocupa o mês e fica fora de todos os totais (decisão D2).
            tipo_linha = 'bonificada'
            status = BillingStatus.CANCELED
            valor = linha.centavos
            notas += f'\nBonificada integralmente no SGR (desconto de {_brl(desconto)}): nada a cobrar.'
            stats.linhas_bonificadas += 1

        periodo = mapped.get('period_label')
        contrato = destino.contrato
        if (
            contrato and periodo and billing_type in RECURRING_BILLING_TYPES
            and existing_recurring_periods(db, contrato.id, [periodo])
        ):
            if status == BillingStatus.CANCELED:
                # Reemissão cancelada (ou bonificação) de um mês que já tem o
                # boleto que valeu: a linha fica registrada, sem cobrança.
                db.add(SgrDocumentoLinha(
                    documento_id=documento.id, chave_linha=linha.chave, placa=linha.placa, produto=linha.produto,
                    mes_referente=linha.mes, tipo='bonificada' if tipo_linha == 'bonificada' else 'descartada',
                    valor_origem_centavos=linha.centavos, desconto_alocado_centavos=desconto,
                ))
                stats.billings_replaced += 1
                continue
            if not _liberar_reemissao(ctx, contrato, periodo):
                # Segundo boleto pago/aberto no mesmo mês: dinheiro real que não
                # pode sumir, mas também não pode virar uma 2ª mensalidade.
                billing_type = 'avulsa'
                notas = (f'Mensalidade {periodo} em duplicidade no SGR — o mês já tem outra '
                         f'cobrança lançada; importada como avulsa. {notas}')
                stats.skip(
                    f"cobrança SGR {mapped.get('title')} ({periodo}): 2º boleto "
                    f"{status.value} para o mesmo mês do contrato — importado como avulsa, confira"
                )

        pago = _pago_centavos(mapped) if status == BillingStatus.PAID else None
        billing = Billing(
            client_id=destino.client_id,
            payer_client_id=destino.payer_client_id,
            contract_id=contrato.id if contrato else None,
            vehicle_id=destino.vehicle.id if destino.vehicle else None,
            title=_trunc(mapped.get('title'), 160),
            billing_type=billing_type,
            amount=_reais(valor),
            due_date=_to_date(mapped.get('due_date')),
            payment_date=_to_date(mapped.get('payment_date')),
            paid_amount=_reais(pago) if pago else None,
            payment_method=_trunc(mapped.get('payment_method'), 40),
            receipt_number=_trunc(mapped.get('receipt_number'), 40),
            installment_number=mapped.get('installment_number'),
            installment_total=mapped.get('installment_total'),
            period_label=_trunc(periodo, 20),
            status=status,
            sgr_payload=mapped.get('sgr_payload'),
            notes=notas,
        )
        db.add(billing)
        # A sessão é autoflush=False: sem o flush, a checagem de mês ocupado
        # não enxerga o que acabou de ser inserido no mesmo documento.
        db.flush()
        db.add(SgrDocumentoLinha(
            documento_id=documento.id, chave_linha=linha.chave, placa=linha.placa, produto=linha.produto,
            mes_referente=linha.mes, tipo=tipo_linha, valor_origem_centavos=linha.centavos,
            desconto_alocado_centavos=desconto, billing_id=billing.id, hash_local=impressao_local(billing),
        ))
        db.flush()
        stats.billings_created += 1
    stats.documentos_conciliados += 1


def _aplicar_alvo(
    db: Session, billing: Billing, alvo: dict, justificativa: str, *, user_id: int | None = None,
) -> list[str]:
    """Leva a cobrança ao estado `alvo` da origem, sob trava e pela política
    bancária única. Devolve os campos alterados. Levanta
    PoliticaBancariaError / ValueError quando a transição não é permitida."""
    travada = lock_billings_for_update(db, [billing.id])
    if not travada or travada[0].is_deleted:
        raise ValueError(f'Cobrança #{billing.id} não está disponível.')
    billing = travada[0]
    novo_status = BillingStatus(alvo['status'])
    atual, novo = _grupo(billing.status), _grupo(novo_status)
    novo_vencimento = _to_date(alvo.get('vencimento'))
    mudancas: list[tuple[str, object, object]] = []

    if atual == 'aberto' and novo == 'pago':
        titulo_bancario.exigir(db, titulo_bancario.RECEBER, [billing.id])
        pago = alvo.get('pago_centavos')
        mudancas += [
            ('status', billing.status.value, BillingStatus.PAID.value),
            ('payment_date', billing.payment_date, _to_date(alvo.get('pagamento'))),
            ('paid_amount', billing.paid_amount, _reais(pago) if pago else None),
            ('payment_method', billing.payment_method, alvo.get('forma')),
        ]
    elif atual == 'aberto' and novo == 'cancelado':
        titulo_bancario.exigir(db, titulo_bancario.CANCELAR, [billing.id])
        mudancas.append(('status', billing.status.value, BillingStatus.CANCELED.value))
    elif atual == 'aberto' and novo == 'aberto':
        if novo_vencimento and novo_vencimento != billing.due_date:
            titulo_bancario.exigir(db, titulo_bancario.ALTERAR_VALOR, [billing.id])
            mudancas.append(('due_date', billing.due_date, novo_vencimento))
        if billing.status != novo_status:
            mudancas.append(('status', billing.status.value, novo_status.value))
    elif atual == novo:
        return []
    else:
        raise ValueError(f'transição {atual}→{novo} não é automática')

    alterados = []
    for campo, antes, depois in mudancas:
        if antes == depois:
            continue
        db.add(BillingChangeLog(
            billing_id=billing.id, changed_by_user_id=user_id, field_name=campo,
            previous_value=None if antes is None else str(antes),
            new_value=None if depois is None else str(depois),
            justification=justificativa,
        ))
        if campo == 'status':
            billing.status = BillingStatus(depois)
        else:
            setattr(billing, campo, depois)
        alterados.append(campo)
    if alterados:
        marca = f'[{justificativa}]'
        billing.notes = f'{billing.notes}\n{marca}' if billing.notes else marca
    db.flush()
    return alterados


def _ja_no_alvo(billing: Billing, alvo: dict) -> bool:
    grupo = _grupo(billing.status)
    if grupo != _grupo(alvo['status']):
        return False
    if grupo == 'aberto':
        return billing.due_date == _to_date(alvo.get('vencimento'))
    return True


def _sincronizar_documento(
    ctx: _Contexto, stats: ImportStats, documento: SgrDocumento, linhas: list[LinhaOrigem],
    snapshot: dict, hash_origem: str,
) -> None:
    """Documento já conciliado e a origem mudou: aplica o que a política
    permite, abre conflito para o resto. O hash só avança sem conflito — a
    rodada seguinte reavalia o que ficou pendente."""
    db = ctx.db
    anterior = documento.snapshot_origem or {}
    if anterior.get('linhas') != snapshot['linhas'] or anterior.get('total') != snapshot['total']:
        _conflito(ctx, stats, 'documento', documento.cod_boleto, 'documento_alterado_origem',
                  tabela='sgr_documentos', local_id=documento.id, hash_origem=hash_origem,
                  detalhe={'total_anterior_centavos': anterior.get('total'),
                           'total_novo_centavos': snapshot['total'],
                           'linhas_anteriores': anterior.get('linhas'), 'linhas_novas': snapshot['linhas']})
        stats.skip(f'documento SGR {documento.cod_boleto}: valor/linhas mudaram na origem — conflito aberto, '
                   f'nada alterado')
        return

    por_chave = {linha.chave: linha for linha in linhas}
    justificativa = f'Migração SGR (execução #{ctx.execucao_id or "simulação"}): mudança na origem'
    pendente = False
    registros = db.scalars(select(SgrDocumentoLinha).where(
        SgrDocumentoLinha.documento_id == documento.id, SgrDocumentoLinha.billing_id.is_not(None),
    )).all()
    for registro in registros:
        origem = por_chave.get(registro.chave_linha)
        billing = db.get(Billing, registro.billing_id)
        if origem is None or billing is None:
            continue
        if billing.is_deleted:
            _conflito(ctx, stats, 'documento', registro.chave_linha, 'cobranca_removida_localmente',
                      tabela='billings', local_id=billing.id, hash_origem=hash_origem)
            pendente = True
            continue
        if registro.tipo == 'bonificada':
            continue  # nada devido; não segue a situação do documento
        if _decisao_manter_local(db, 'documento', registro.chave_linha, hash_origem):
            continue  # decidido: o MasterSat prevalece sobre este estado da origem
        alvo = _alvo_da_linha(origem.mapped)
        if impressao_local(billing) != registro.hash_local and _ja_no_alvo(billing, alvo):
            # Mudou dos dois lados para o mesmo lugar (ex.: baixa manual aqui
            # e pagamento lá): nada a decidir.
            registro.hash_local = impressao_local(billing)
            stats.billings_reused += 1
            continue
        if impressao_local(billing) != registro.hash_local:
            conflito = _conflito(
                ctx, stats, 'documento', registro.chave_linha, 'edicao_local', tabela='billings',
                local_id=billing.id, hash_origem=hash_origem,
                detalhe={'status_local': _grupo(billing.status), 'status_origem': _grupo(alvo['status']),
                         'alvo': alvo},
            )
            if conflito is not None:
                pendente = True
                stats.skip(f'cobrança #{billing.id}: alterada no MasterSat e na origem — conflito aberto')
            continue
        try:
            alterados = _aplicar_alvo(db, billing, alvo, justificativa)
        except (titulo_bancario.PoliticaBancariaError, ValueError) as exc:
            tipo = ('titulo_bancario_local' if isinstance(exc, titulo_bancario.PoliticaBancariaError)
                    else 'transicao_nao_automatica')
            conflito = _conflito(
                ctx, stats, 'documento', registro.chave_linha, tipo, tabela='billings', local_id=billing.id,
                hash_origem=hash_origem,
                detalhe={'status_local': _grupo(billing.status), 'status_origem': _grupo(alvo['status']),
                         'alvo': alvo, 'motivo': getattr(exc, 'code', None) or str(exc)[:120]},
            )
            if conflito is not None:
                pendente = True
            continue
        registro.hash_local = impressao_local(billing)
        if alterados:
            stats.billings_updated += 1
        else:
            stats.billings_reused += 1
    if not pendente:
        documento.hash_origem = hash_origem
        documento.snapshot_origem = snapshot
        documento.situacao_origem = _trunc((linhas[0].mapped.get('sgr_payload') or {}).get('situacao'), 40)
    documento.ultima_execucao_id = ctx.execucao_id
    db.flush()


def _importar_documento(
    ctx: _Contexto, stats: ImportStats, client: Client, cod: str, entradas: list[dict],
) -> None:
    db = ctx.db
    base = entradas[0]
    linhas = linhas_do_documento(cod, entradas)
    total = _total_do_documento(base)
    snapshot = _snapshot_documento(base, linhas, total)
    hash_origem = _hash(snapshot)
    documento = db.scalar(select(SgrDocumento).where(SgrDocumento.cod_boleto == cod))
    obrigacoes = sum(1 for linha in linhas if (linha.centavos or 0) > 0)

    if documento is not None and documento.client_id not in (None, client.id):
        _conflito(ctx, stats, 'documento', cod, 'documento_de_outro_cliente', tabela='sgr_documentos',
                  local_id=documento.id, detalhe={'client_id_local': documento.client_id,
                                                  'client_id_origem': client.id})
        stats.billings_skipped += obrigacoes
        return

    if documento is not None and documento.status == 'conciliado':
        if documento.hash_origem == hash_origem:
            stats.documentos_inalterados += 1
            stats.billings_reused += db.query(SgrDocumentoLinha).filter(
                SgrDocumentoLinha.documento_id == documento.id, SgrDocumentoLinha.billing_id.is_not(None),
            ).count()
            documento.ultima_execucao_id = ctx.execucao_id
            return
        _sincronizar_documento(ctx, stats, documento, linhas, snapshot, hash_origem)
        return

    if documento is None or documento.status == 'bloqueado':
        # Documento importado pelo código antigo (antes da Fase 04): nunca
        # recriar — só adotar, ou continuar bloqueado para compensação.
        legados = _legado(ctx, client, cod, base)
        if legados:
            motivo = adotar_legado(ctx, stats, client, cod, base, linhas, total, snapshot, hash_origem, legados)
            if motivo:
                stats.documentos_bloqueados += 1
                stats.contar('bloqueios', motivo)
                stats.skip(f'documento SGR {cod}: importado antes da Fase 04 e não reconciliável '
                           f'automaticamente ({motivo}) — ver scripts/sgr_backfill.py')
            return

    for linha in linhas:
        if (linha.centavos or 0) > 0 and not linha.mapped.get('due_date'):
            _bloquear_documento(ctx, stats, documento, cod, client, base, total, snapshot, hash_origem,
                                'sem_vencimento', {}, obrigacoes)
            return

    plano, motivo, detalhe = conciliar(linhas, total)
    if motivo:
        _bloquear_documento(ctx, stats, documento, cod, client, base, total, snapshot, hash_origem,
                            motivo, detalhe, obrigacoes)
        return

    destinos: dict[str, _Destino] = {}
    for linha in linhas:
        if (linha.centavos or 0) <= 0:
            continue
        destino = _destino_da_linha(ctx, client, linha.placa)
        if destino.motivo:
            _bloquear_documento(ctx, stats, documento, cod, client, base, total, snapshot, hash_origem,
                                destino.motivo, {'linha': linha.chave}, obrigacoes)
            return
        destinos[linha.chave] = destino

    _criar_cobrancas(ctx, stats, client, cod, base, linhas, plano, destinos, total, snapshot, hash_origem,
                     documento)


def _import_billings(ctx: _Contexto, stats: ImportStats, node: ClientNode, client: Client) -> None:
    """Histórico de cobrança do cliente, documento a documento.

    Um mês de contrato comporta UMA mensalidade (índice único
    uq_billings_contract_competencia_recorrente). No SGR o mesmo mês costuma
    ter vários boletos — reemissões e trocas de carnê (caso real: ARV0682 em
    03/2021 com 5888 REMOVIDO, 5950 CANCELADO, 7761 REMOVIDO e 7787 pago). Os
    documentos são processados do melhor para o pior (pago > aberto >
    cancelado, mais novo primeiro), então o que ocupa o mês é o que valeu.
    """
    grupos: dict[str, list[dict]] = {}
    for mapped in node.billings:
        cod = _cod_documento(mapped)
        if cod is None:
            stats.billings_skipped += 1
            stats.skip(f"cobrança SGR ({mapped.get('period_label')}): sem código de boleto — não identificável")
            continue
        grupos.setdefault(cod, []).append(mapped)
    # Documentos já conciliados sincronizam ANTES dos novos: numa reemissão, o
    # boleto antigo precisa estar cancelado quando o novo pedir o mês.
    conhecidos = set(ctx.db.scalars(select(SgrDocumento.cod_boleto).where(
        SgrDocumento.cod_boleto.in_(list(grupos)), SgrDocumento.status == 'conciliado',
    )).all()) if grupos else set()
    ordem = sorted(grupos.items(), key=lambda item: (
        item[0] not in conhecidos, min(_prioridade_no_mes(m) for m in item[1]),
    ))
    for cod, entradas in ordem:
        _importar_documento(ctx, stats, client, cod, entradas)
        base = entradas[0]
        if base.get('link_boleto'):
            _enfileirar_arquivo(ctx, stats, 'boleto_pdf', cod, client.id, base.get('link_boleto'),
                                f'boleto-sgr-{cod}.pdf', _BOLETO_CATEGORIA)


# ---------------------------------------------------------------------------
# Outbox de arquivos
# ---------------------------------------------------------------------------

_CHAVE_ARQUIVO = re.compile(r'[^A-Za-z0-9_.-]+')


def chave_objeto(client_id: int, tipo: str, chave: str) -> str:
    extensao = 'pdf' if tipo == 'boleto_pdf' else 'xml'
    return f'clients/{client_id}/documents/sgr-{tipo}-{_CHAVE_ARQUIVO.sub("_", chave)[:80]}.{extensao}'


def _enfileirar_arquivo(
    ctx: _Contexto, stats: ImportStats, tipo: str, chave: str, client_id: int, url: str | None,
    nome_arquivo: str, categoria: str,
) -> None:
    """Pede o arquivo (idempotente). Reaproveita o documento que o importador
    antigo já tinha gravado para o mesmo arquivo — nada é baixado de novo."""
    from app.services.sgr_migration.download import host_da_url

    db = ctx.db
    reuso, pulado = ('boletos_reused', 'boletos_skipped') if tipo == 'boleto_pdf' else ('invoices_reused', 'invoices_skipped')
    arquivo = db.scalar(select(SgrArquivo).where(SgrArquivo.tipo == tipo, SgrArquivo.chave_origem == chave))
    if arquivo is not None:
        if arquivo.status == 'baixado':
            setattr(stats, reuso, getattr(stats, reuso) + 1)
            return
        if url and arquivo.url != url:
            arquivo.url = url
            arquivo.tentativas = 0
        if url and arquivo.status in ('sem_url', 'bloqueado'):
            arquivo.status = 'pendente'
    else:
        legado = db.scalar(select(Document).where(
            Document.reference_type == 'client', Document.reference_id == client_id,
            Document.category == categoria, Document.file_name == nome_arquivo, Document.active.is_(True),
        ).limit(1))
        arquivo = SgrArquivo(
            tipo=tipo, chave_origem=chave[:120], client_id=client_id, url=url,
            status='baixado' if legado else ('pendente' if url else 'sem_url'),
            object_key=legado.object_key if legado else chave_objeto(client_id, tipo, chave),
            document_id=legado.id if legado else None,
            tamanho_bytes=legado.size_bytes if legado else None,
        )
        db.add(arquivo)
        db.flush()
        if legado:
            setattr(stats, reuso, getattr(stats, reuso) + 1)
            return
    if arquivo.status == 'sem_url':
        setattr(stats, pulado, getattr(stats, pulado) + 1)
        return
    stats.arquivos_pendentes += 1
    host = host_da_url(url)
    if host:
        stats.contar('hosts_arquivos', host)


def _import_invoices(ctx: _Contexto, stats: ImportStats, node: ClientNode, client: Client) -> None:
    """Pede o XML da NFS-e como documento do cliente.

    Não usa a tabela nfse_notas de propósito: lá `billing_id` é único (uma
    nota por cobrança), e no SGR uma nota cobre o boleto consolidado inteiro
    — que aqui virou várias cobranças, uma por placa. Escolher uma delas para
    receber a nota inventaria um vínculo que não existe.
    """
    for nota in node.invoices:
        numero = nota.get('numero_nf')
        chave = f"{nota.get('cod_boleto') or '-'}:{numero}"
        antes = stats.invoices_skipped
        _enfileirar_arquivo(ctx, stats, 'nfse_xml', chave, client.id, nota.get('url'),
                            f'nfse-{numero}.xml', _NFSE_CATEGORIA)
        if stats.invoices_skipped > antes:
            stats.skip(f"nota fiscal {numero}: {nota.get('erro') or 'sem XML disponível'}")


def processar_arquivos(
    db: Session, stats: ImportStats | None = None, *, baixar=None, upload=None, validar_destino=None,
    client_ids: set[int] | None = None,
) -> ImportStats:
    """Baixa os arquivos pendentes, cada um numa transação curta.

    Ordem: download controlado → validação do conteúdo → upload na chave
    estável → documento + status numa transação. Se o banco falhar depois do
    upload, a linha continua pendente e a próxima tentativa regrava o MESMO
    objeto e cria o documento — converge sem objeto órfão.
    """
    from app.core.config import settings
    from app.services.sgr_migration import download

    stats = stats or ImportStats()
    baixar = baixar or download.baixar
    validar_destino = validar_destino or download.validar_url
    if upload is None:
        from app.services.storage import upload_bytes as upload

    consulta = select(SgrArquivo.id).where(
        SgrArquivo.status.in_(('pendente', 'falhou', 'bloqueado')),
        SgrArquivo.tentativas < settings.sgr_download_max_tentativas,
    ).order_by(SgrArquivo.id)
    if client_ids is not None:
        consulta = consulta.where(SgrArquivo.client_id.in_(client_ids))
    for arquivo_id in db.scalars(consulta).all():
        arquivo = db.get(SgrArquivo, arquivo_id)
        ok, pulado = ('boletos_created', 'boletos_skipped') if arquivo.tipo == 'boleto_pdf' else ('invoices_created', 'invoices_skipped')
        rotulo = f'{"boleto" if arquivo.tipo == "boleto_pdf" else "nota fiscal"} {arquivo.chave_origem}'
        if not arquivo.url:
            arquivo.status = 'sem_url'
            db.commit()
            continue
        try:
            validar_destino(arquivo.url)
            baixado = baixar(arquivo.url)
            if arquivo.tipo == 'boleto_pdf':
                download.validar_pdf(baixado.conteudo)
            else:
                download.validar_xml(baixado.conteudo)
        except download.DownloadRecusado as exc:
            destino = exc.motivo in ('esquema_nao_https', 'credencial_na_url', 'url_sem_host', 'url_invalida',
                                     'porta_nao_padrao', 'host_nao_permitido', 'ip_nao_publico',
                                     'redirects_demais', 'redirect_sem_destino')
            arquivo.status = 'bloqueado' if destino else 'falhou'
            arquivo.tentativas += 0 if destino else 1
            arquivo.ultimo_erro = exc.motivo[:255]
            db.commit()
            if destino:
                stats.arquivos_bloqueados += 1
            setattr(stats, pulado, getattr(stats, pulado) + 1)
            stats.skip(f'{rotulo}: download recusado ({exc.motivo})')
            continue
        except download.DownloadFalhou as exc:
            arquivo.status = 'falhou'
            arquivo.tentativas += 1
            arquivo.ultimo_erro = str(exc)[:255]
            db.commit()
            setattr(stats, pulado, getattr(stats, pulado) + 1)
            stats.skip(f'{rotulo}: falha ao baixar ({str(exc)[:80]}) — fica pendente para a próxima rodada')
            continue

        tipo_conteudo = 'application/pdf' if arquivo.tipo == 'boleto_pdf' else 'application/xml'
        try:
            upload(object_name=arquivo.object_key, content=baixado.conteudo, content_type=tipo_conteudo)
        except Exception as exc:  # noqa: BLE001 — falha de storage fica registrada e é retomada
            arquivo.status = 'falhou'
            arquivo.tentativas += 1
            arquivo.ultimo_erro = f'storage: {type(exc).__name__}'
            db.commit()
            setattr(stats, pulado, getattr(stats, pulado) + 1)
            stats.skip(f'{rotulo}: falha ao gravar no storage ({type(exc).__name__}) — fica pendente')
            continue

        try:
            documento = db.scalar(select(Document).where(Document.object_key == arquivo.object_key))
            if documento is None:
                nome = (f'boleto-sgr-{arquivo.chave_origem}.pdf' if arquivo.tipo == 'boleto_pdf'
                        else f'nfse-{arquivo.chave_origem.split(":")[-1]}.xml')
                documento = Document(
                    file_name=nome, object_key=arquivo.object_key, content_type=tipo_conteudo,
                    size_bytes=len(baixado.conteudo), reference_type='client', reference_id=arquivo.client_id,
                    category=_BOLETO_CATEGORIA if arquivo.tipo == 'boleto_pdf' else _NFSE_CATEGORIA, active=True,
                )
                db.add(documento)
                db.flush()
            arquivo.document_id = documento.id
            arquivo.status = 'baixado'
            arquivo.sha256 = baixado.sha256
            arquivo.tamanho_bytes = len(baixado.conteudo)
            arquivo.ultimo_erro = None
            db.commit()
        except Exception as exc:  # noqa: BLE001 — objeto na chave estável; a próxima rodada converge
            db.rollback()
            logger.warning('SGR: documento do arquivo %s não gravado após upload (%s)', arquivo_id, type(exc).__name__)
            setattr(stats, pulado, getattr(stats, pulado) + 1)
            stats.skip(f'{rotulo}: gravado no storage mas o registro falhou ({type(exc).__name__}) — '
                       f'a próxima rodada reaproveita o mesmo objeto')
            continue
        setattr(stats, ok, getattr(stats, ok) + 1)
    return stats


def _previa_arquivos(db: Session, stats: ImportStats) -> None:
    """Simulação: o que seria baixado, sem rede. Só a regra de destino que
    não depende de DNS é aplicada (esquema, porta, allowlist de host)."""
    from app.services.sgr_migration import download

    for arquivo in db.scalars(select(SgrArquivo).where(SgrArquivo.status == 'pendente')).all():
        ok, pulado = ('boletos_created', 'boletos_skipped') if arquivo.tipo == 'boleto_pdf' else ('invoices_created', 'invoices_skipped')
        try:
            download.validar_url(arquivo.url or '')
        except download.DownloadRecusado as exc:
            stats.arquivos_bloqueados += 1
            setattr(stats, pulado, getattr(stats, pulado) + 1)
            stats.skip(f'arquivo {arquivo.chave_origem}: seria recusado ({exc.motivo})')
            continue
        setattr(stats, ok, getattr(stats, ok) + 1)


# ---------------------------------------------------------------------------
# Manifesto e reconciliação
# ---------------------------------------------------------------------------

def montar_manifesto(db: Session, result: PocRunResult, stats: ImportStats) -> dict:
    """Compara origem × MasterSat por documento e por (cliente, competência,
    grupo de status), em centavos. Sem nome, CPF ou placa — só códigos.

    Critério de aceite: diferença zero em todo documento conciliado; todo
    documento que não fecha está bloqueado e listado.
    """
    diferencas: list[dict] = []
    agregado: dict[tuple, dict] = {}
    documentos_origem = 0
    for node in result.clients:
        cod_cliente = str(node.mapped.get('external_id') or '?')
        grupos: dict[str, list[dict]] = {}
        for mapped in node.billings:
            cod = _cod_documento(mapped)
            if cod:
                grupos.setdefault(cod, []).append(mapped)
        for cod, entradas in grupos.items():
            documentos_origem += 1
            documento = db.scalar(select(SgrDocumento).where(SgrDocumento.cod_boleto == cod))
            linhas = linhas_do_documento(cod, entradas)
            total = _total_do_documento(entradas[0])
            grupo_origem = _grupo(entradas[0].get('status'))
            if documento is None or documento.status != 'conciliado':
                continue
            plano, _motivo, _det = conciliar(linhas, total)
            registros = {r.chave_linha: r for r in db.scalars(select(SgrDocumentoLinha).where(
                SgrDocumentoLinha.documento_id == documento.id)).all()}
            local_total = 0
            for linha in linhas:
                if (linha.centavos or 0) <= 0:
                    continue
                registro = registros.get(linha.chave)
                billing = db.get(Billing, registro.billing_id) if registro and registro.billing_id else None
                competencia = linha.mes or '-'
                chave_ag = (cod_cliente, competencia, grupo_origem)
                ag = agregado.setdefault(chave_ag, {'origem_centavos': 0, 'local_centavos': 0})
                esperado = plano.liquido.get(linha.chave, 0) if plano else linha.centavos
                if grupo_origem != 'cancelado':
                    ag['origem_centavos'] += esperado
                if billing is not None and not billing.is_deleted and _grupo(billing.status) != 'cancelado':
                    valor = _centavos_do_banco(billing.amount)
                    local_total += valor
                    ag['local_centavos'] += valor
                    if _grupo(billing.status) != grupo_origem and grupo_origem != 'cancelado':
                        diferencas.append({'cod_cliente': cod_cliente, 'cod_boleto': cod, 'linha': linha.indice,
                                           'tipo': 'status_divergente', 'origem': grupo_origem,
                                           'local': _grupo(billing.status)})
            esperado_total = 0 if grupo_origem == 'cancelado' else (total or 0)
            if local_total != esperado_total:
                diferencas.append({'cod_cliente': cod_cliente, 'cod_boleto': cod, 'tipo': 'valor_divergente',
                                   'origem_centavos': esperado_total, 'local_centavos': local_total})

    bloqueados = [
        {'cod_boleto': d.cod_boleto, 'cod_cliente': d.cod_cliente, 'motivo': d.motivo_bloqueio,
         'total_origem_centavos': d.total_origem_centavos}
        for d in db.scalars(select(SgrDocumento).where(SgrDocumento.status == 'bloqueado')
                            .order_by(SgrDocumento.cod_boleto)).all()
    ]
    conflitos_abertos: dict[str, int] = defaultdict(int)
    for tipo in db.scalars(select(SgrConflito.tipo).where(SgrConflito.status == 'aberto')).all():
        conflitos_abertos[tipo] += 1
    arquivos: dict[str, int] = defaultdict(int)
    for status in db.scalars(select(SgrArquivo.status)).all():
        arquivos[status] += 1
    coletas_incompletas = [
        c for c in result.coletas
        if not c.get('completa') and not str(c.get('observacao') or '').startswith('leitura interrompida')
    ]
    return {
        'gerado_em': datetime.now(timezone.utc).isoformat(),
        'coletas': {'total': len(result.coletas), 'incompletas': coletas_incompletas},
        'clientes_com_coleta_incompleta': [
            str(n.mapped.get('external_id') or '?') for n in result.clients if n.coleta_incompleta
        ],
        'documentos_origem': documentos_origem,
        'documentos_bloqueados': bloqueados,
        'diferencas': diferencas,
        'por_cliente_competencia_status': [
            {'cod_cliente': k[0], 'competencia': k[1], 'status': k[2], **v,
             'diferenca_centavos': v['local_centavos'] - v['origem_centavos']}
            for k, v in sorted(agregado.items())
        ],
        'conflitos_abertos': dict(conflitos_abertos),
        'arquivos': dict(arquivos),
        'contagens': stats.resumo(),
    }


def _status_da_execucao(manifesto: dict, stats: ImportStats) -> str:
    if manifesto['coletas']['incompletas'] or manifesto['clientes_com_coleta_incompleta'] or stats.unidades_falharam:
        return 'incompleta'
    if (manifesto['documentos_bloqueados'] or manifesto['diferencas'] or manifesto['conflitos_abertos']
            or manifesto['arquivos'].get('pendente') or manifesto['arquivos'].get('falhou')
            or manifesto['arquivos'].get('bloqueado')):
        return 'concluida_com_pendencias'
    return 'concluida'


# ---------------------------------------------------------------------------
# Orquestração por unidade
# ---------------------------------------------------------------------------

def _importar_cliente(ctx: _Contexto, stats: ImportStats, node: ClientNode) -> str:
    client = _resolver_cliente(ctx, stats, node)
    if client is None:
        return 'aplicada'
    for vnode in node.vehicles:
        vehicle = _resolver_veiculo(ctx, stats, client, vnode)
        if vehicle is None:
            continue
        plate = vnode.mapped.get('plate')
        for tnode in vnode.trackers:
            tracker = _resolver_rastreador(ctx, stats, client, vehicle, tnode, plate)
            # O contrato sai do MESMO registro de vínculo que gera o
            # rastreador, e não depende de ele ter entrado: no SGR existe
            # veículo com cobrança ativa cujo equipamento está sem IMEI.
            if tnode.contract:
                _import_contract(ctx, stats, tnode.contract, client, vehicle,
                                 tracker.id if tracker else None, plate)
    # Por último: o histórico de cobrança precisa dos veículos já gravados
    # para resolver a placa de cada linha da discriminação do boleto.
    _import_billings(ctx, stats, node, client)
    _import_invoices(ctx, stats, node, client)
    return 'aplicada'


def _hash_unidade(node: ClientNode) -> str:
    return _hash({'raw': node.raw, 'mapped': node.mapped, 'billings': node.billings, 'invoices': node.invoices,
                  'veiculos': [[v.mapped, [[t.mapped, t.contract] for t in v.trackers]] for v in node.vehicles]})


def _rodar_unidade(ctx: _Contexto, stats: ImportStats, unidade: str, hash_origem: str, executar) -> str:
    parcial = ImportStats()
    ctx.nova_unidade()
    if ctx.dry_run:
        try:
            executar(parcial)
        except _Bloqueio as exc:
            parcial.clientes_bloqueados += 1
            parcial.skip(f'{unidade}: bloqueado ({exc.motivo}) — conflito aberto, nada importado')
        stats.merge(parcial)
        return 'aplicada'
    try:
        try:
            status, erro = executar(parcial), None
        except _Bloqueio as exc:
            status, erro = 'bloqueada', exc.motivo
            parcial.clientes_bloqueados += 1
            parcial.skip(f'{unidade}: bloqueado ({exc.motivo}) — conflito aberto, nada importado')
        ctx.db.add(SgrExecucaoUnidade(
            execucao_id=ctx.execucao_id, unidade=unidade[:80], status=status, hash_origem=hash_origem,
            resumo=parcial.resumo(), erro=erro,
        ))
        ctx.db.commit()
        stats.merge(parcial)
        return status
    except Exception as exc:  # noqa: BLE001 — a unidade falha sozinha; as confirmadas ficam
        ctx.db.rollback()
        logger.exception('SGR: unidade %s falhou', unidade)
        ctx.db.add(SgrExecucaoUnidade(
            execucao_id=ctx.execucao_id, unidade=unidade[:80], status='falhou', hash_origem=hash_origem,
            erro=f'{type(exc).__name__}: {str(exc)[:500]}',
        ))
        ctx.db.commit()
        stats.unidades_falharam += 1
        stats.skip(f'{unidade}: falhou ({type(exc).__name__}) — nada desta unidade foi gravado; rode de novo')
        return 'falhou'


def import_poc_result(
    db: Session, result: PocRunResult, dry_run: bool = True, *,
    retomar_de: int | None = None, parametros: dict | None = None,
    baixar=None, upload=None, processar_downloads: bool = True,
) -> ImportStats:
    """Insere/sincroniza no MasterSat o que foi lido do SGR.

    `dry_run`: mesmo caminho, flush no lugar de commit e rollback no fim —
    mostra o que seria criado, bloqueado e conflitado, inclusive erros de
    constraint, sem deixar nada no banco e sem baixar/enviar arquivo algum.

    `retomar_de`: id de uma execução anterior; unidades já aplicadas lá com o
    mesmo dado de origem são puladas (checkpoint).
    """
    stats = ImportStats()
    execucao = None
    ja_aplicadas: dict[str, str] = {}
    if not dry_run:
        execucao = SgrExecucao(
            status='em_andamento', parametros=parametros or {}, retoma_execucao_id=retomar_de,
            versao_codigo=_versao_codigo(),
        )
        db.add(execucao)
        db.commit()
        stats.execucao_id = execucao.id
        if retomar_de:
            ja_aplicadas = {
                u.unidade: u.hash_origem
                for u in db.scalars(select(SgrExecucaoUnidade).where(
                    SgrExecucaoUnidade.execucao_id == retomar_de,
                    SgrExecucaoUnidade.status.in_(('aplicada', 'reaproveitada')),
                )).all()
            }
    ctx = _Contexto(db=db, dry_run=dry_run, execucao=execucao)

    def _planos(parcial: ImportStats) -> str:
        ctx.plano_por_codigo = _import_plans(ctx, parcial, result)
        return 'aplicada'

    if _rodar_unidade(ctx, stats, 'planos', _hash(result.plans), _planos) == 'falhou':
        # Sem planos nenhum contrato pode ser criado: não adianta seguir.
        stats.status_execucao = 'falhou'
        execucao = db.get(SgrExecucao, execucao.id)
        execucao.status = 'falhou'
        execucao.concluida_em = datetime.now(timezone.utc)
        db.commit()
        return stats

    for node in result.clients:
        codigo = node.mapped.get('external_id') or (node.raw or {}).get('cod_cliente') or '?'
        unidade = f'cliente:{codigo}'
        if node.fetch_failed:
            stats.clients_skipped += 1
            stats.skip(f"cliente SGR #{codigo}: falhou no mapeamento")
            continue
        if node.coleta_incompleta:
            stats.clientes_bloqueados += 1
            stats.contar('bloqueios', 'coleta_incompleta')
            stats.skip(f'cliente SGR #{codigo}: coleta incompleta ({"; ".join(node.coleta_incompleta)}) — '
                       f'não importado; rode de novo quando o SGR responder')
            if execucao is not None:
                db.add(SgrExecucaoUnidade(execucao_id=execucao.id, unidade=unidade[:80], status='bloqueada',
                                          erro='coleta_incompleta: ' + '; '.join(node.coleta_incompleta)[:400]))
                db.commit()
            continue
        hash_origem = _hash_unidade(node)
        if ja_aplicadas.get(unidade) == hash_origem:
            stats.unidades_reaproveitadas += 1
            db.add(SgrExecucaoUnidade(execucao_id=execucao.id, unidade=unidade[:80], status='reaproveitada',
                                      hash_origem=hash_origem))
            db.commit()
            continue
        _rodar_unidade(ctx, stats, unidade, hash_origem, lambda parcial, n=node: _importar_cliente(ctx, parcial, n))

    if dry_run:
        _previa_arquivos(db, stats)
    elif processar_downloads:
        processar_arquivos(db, stats, baixar=baixar, upload=upload)

    stats.manifesto = montar_manifesto(db, result, stats)
    stats.status_execucao = _status_da_execucao(stats.manifesto, stats)
    if dry_run:
        db.rollback()
    else:
        execucao = db.get(SgrExecucao, execucao.id)
        execucao.status = stats.status_execucao
        execucao.manifesto = stats.manifesto
        execucao.concluida_em = datetime.now(timezone.utc)
        db.commit()
    return stats
