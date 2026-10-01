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
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.billing import Billing
from app.models.client import Client
from app.models.document import Document
from app.models.contract import Contract
from app.models.enums import BillingStatus, ClientStatus, TrackerStatus, VehicleStatus
from app.models.plan import Plan
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.services.financial import RECURRING_BILLING_TYPES, existing_recurring_periods
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
    # Reemissões canceladas/removidas de uma mensalidade cujo mês já tem o
    # boleto que valeu (ver _import_billings) — só contagem, sem 1 aviso cada.
    billings_replaced: int = 0
    invoices_created: int = 0
    invoices_reused: int = 0
    invoices_skipped: int = 0
    boletos_created: int = 0
    boletos_reused: int = 0
    boletos_skipped: int = 0
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


def _valor_pago_valido(value) -> float | None:
    """paid_amount só entra se for > 0 (regra do schema BillingOut).

    O SGR manda valor_pagamento '0,00' em boleto não pago, o que no MasterSat
    é ausência de pagamento. Gravar 0.0 não estraga só aquele registro: o
    schema valida na RESPOSTA, então a LISTAGEM INTEIRA de cobranças passa a
    responder 500 (mesmo efeito que o RENAVAM placeholder teve no /vehicles).
    """
    if value in (None, ''):
        return None
    try:
        numero = float(value)
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None


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

    existente = _find_contract(db, client_id, vehicle_id)
    if existente:
        # Rodada anterior criou o contrato quando o equipamento ainda vinha sem
        # IMEI (rastreador não entrou). Agora que o rastreador existe, liga —
        # sem isto a tela mostra "Sem plano" para sempre. Só preenche o vazio:
        # contrato já ligado a outro rastreador nunca é trocado aqui.
        if existente.tracker_id is None and tracker_id is not None:
            existente.tracker_id = tracker_id
            db.flush()
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


def _import_billings(
    db: Session, node: ClientNode, client_id: int, stats: ImportStats, dry_run: bool,
) -> None:
    """Grava o histórico de cobrança do cliente.

    Cada entrada já vem achatada por linha de discriminação (ver
    _fetch_boletos). O veículo é resolvido pela placa; quando a placa não foi
    migrada (máquina sem placa no padrão), a cobrança entra sem veículo em vez
    de ser descartada — o valor continua fazendo parte do histórico do cliente.

    Dedup por (cliente, nosso_numero, título, competência, valor). O título
    carrega a placa de origem (ver map_boleto), e sem ela duas linhas de
    placas diferentes que não foram migradas ficariam idênticas e uma seria
    descartada — perdendo valor real. O valor entra na chave porque a mesma
    placa aparece mais de uma vez no mesmo boleto quando há ajuste (ex.:
    mensalidade 49,99, desconto -41,65 e serviço 120,00 no mesmo documento).

    Um mês de contrato comporta UMA mensalidade (índice único
    uq_billings_contract_period_recurring). No SGR o mesmo mês costuma ter
    vários boletos — reemissões e trocas de carnê (caso real: ARV0682 em
    03/2021 com 5888 REMOVIDO, 5950 CANCELADO, 7761 REMOVIDO e 7787 pago).
    Os boletos são processados do melhor para o pior (pago > aberto >
    cancelado, mais novo primeiro), então o que ocupa o mês é o que valeu.
    """
    for mapped in sorted(node.billings, key=_prioridade_no_mes):
        if mapped.get('amount') is None or not mapped.get('due_date'):
            stats.billings_skipped += 1
            stats.skip(
                f"cobrança SGR #{mapped.get('external_id')}: sem valor ou sem vencimento"
            )
            continue

        if mapped['amount'] <= 0:
            # Linha de desconto/abatimento dentro do boleto consolidado. O
            # MasterSat não modela cobrança negativa (BillingOut exige
            # amount > 0) e gravar assim derruba a listagem inteira. Fica de
            # fora e é reportado: a soma das cobranças do cliente vai ficar
            # maior que a do boleto original nesses casos.
            stats.billings_skipped += 1
            stats.skip(
                f"cobrança SGR #{mapped.get('external_id')} ({mapped.get('period_label')}): "
                f"valor negativo {mapped['amount']} (desconto/abatimento) — o MasterSat não "
                f"comporta cobrança negativa"
            )
            continue

        placa = mapped.get('vehicle_plate')
        veiculo = _find_vehicle(db, placa) if placa else None
        # O contrato daquele veículo é o que liga a cobrança ao plano na tela
        # do financeiro; sem ele a listagem mostra a cobrança sem plano nem
        # status de contrato.
        contrato = _find_contract(db, client_id, veiculo.id) if veiculo else None

        existente = (
            db.query(Billing)
            .filter(
                Billing.client_id == client_id,
                Billing.receipt_number == mapped.get('receipt_number'),
                Billing.title == mapped.get('title'),
                Billing.period_label == mapped.get('period_label'),
                Billing.amount == mapped['amount'],
                Billing.is_deleted.is_(False),
            )
            .first()
        )
        if existente:
            stats.billings_reused += 1
            continue

        # 'ABERTO' no SGR não diz se já venceu — quem classifica isso no
        # MasterSat é o status VENCIDA, que é o que as telas de pendência e
        # inadimplência filtram. Sem esta conversão, cobrança vencida importada
        # fica invisível nessas telas.
        status = BillingStatus(mapped.get('status') or BillingStatus.PENDING.value)
        vencimento = _to_date(mapped.get('due_date'))
        if status == BillingStatus.PENDING and vencimento and vencimento < date.today():
            status = BillingStatus.OVERDUE

        billing_type = mapped.get('billing_type') or 'recorrente'
        notas = _nota_cobranca(mapped)
        periodo = mapped.get('period_label')
        if (
            contrato and periodo and billing_type in RECURRING_BILLING_TYPES
            and existing_recurring_periods(db, contrato.id, [periodo])
        ):
            if status == BillingStatus.CANCELED:
                stats.billings_replaced += 1
                continue
            # Segundo boleto pago/aberto no mesmo mês: dinheiro real que não
            # pode sumir, mas também não pode virar uma 2ª mensalidade.
            billing_type = 'avulsa'
            notas = (f'Mensalidade {periodo} em duplicidade no SGR — o mês já tem outra '
                     f'cobrança lançada; importada como avulsa. {notas}')
            stats.skip(
                f"cobrança SGR {mapped.get('title')} ({periodo}): 2º boleto "
                f"{status.value} para o mesmo mês do contrato — importado como avulsa, confira"
            )

        db.add(Billing(
            client_id=client_id,
            contract_id=contrato.id if contrato else None,
            vehicle_id=veiculo.id if veiculo else None,
            title=_trunc(mapped.get('title'), 160),
            billing_type=billing_type,
            amount=mapped['amount'],
            due_date=_to_date(mapped.get('due_date')),
            payment_date=_to_date(mapped.get('payment_date')),
            paid_amount=_valor_pago_valido(mapped.get('paid_amount')),
            payment_method=_trunc(mapped.get('payment_method'), 40),
            receipt_number=_trunc(mapped.get('receipt_number'), 40),
            installment_number=mapped.get('installment_number'),
            installment_total=mapped.get('installment_total'),
            period_label=_trunc(mapped.get('period_label'), 20),
            status=status,
            sgr_payload=mapped.get('sgr_payload'),
            notes=notas,
        ))
        # A sessão é autoflush=False: sem o flush, a consulta de dedup acima
        # não enxerga o que acabou de ser inserido e o mesmo boleto entra
        # várias vezes dentro de uma única execução.
        db.flush()
        stats.billings_created += 1

        _import_boleto_pdf(db, mapped, client_id, stats, dry_run)


_RANK_STATUS = {
    BillingStatus.PAID.value: 0,
    BillingStatus.PENDING.value: 1,
    BillingStatus.OVERDUE.value: 1,
}


def _prioridade_no_mes(mapped: dict) -> tuple:
    try:
        cod = -int((mapped.get('sgr_payload') or {}).get('cod_boleto') or 0)
    except (TypeError, ValueError):
        cod = 0
    return (_RANK_STATUS.get(mapped.get('status') or '', 2), cod)


_NFSE_CATEGORIA = 'nota_fiscal'
_BOLETO_CATEGORIA = 'boleto'


def _nota_cobranca(mapped: dict) -> str:
    """Observação da cobrança, com a linha digitável quando o boleto está aberto.

    A linha digitável e o código de barras não têm coluna própria em
    `billings` (no MasterSat isso vive em ailos_boletos, que é da emissão
    pelo Ailos e não comporta boleto de outra origem). Guardar na observação
    mantém o dado acessível sem forçar um modelo que não é dele.
    """
    partes = ['Importado do SGR (Hinova).']
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


def _import_boleto_pdf(
    db: Session, mapped: dict, client_id: int, stats: ImportStats, dry_run: bool,
) -> None:
    """Baixa o PDF do boleto em aberto e guarda como documento do cliente.

    Só existe para boleto ABERTO: depois de baixado o SGR não devolve mais
    `link`. Um boleto por documento — a mesma URL se repete nas várias linhas
    de discriminação, por isso a deduplicação é pelo nome do arquivo.
    """
    url = mapped.get('link_boleto')
    if not url:
        return

    nome_arquivo = f"boleto-sgr-{mapped.get('external_id')}.pdf"
    ja_existe = (
        db.query(Document)
        .filter(
            Document.reference_type == 'client',
            Document.reference_id == client_id,
            Document.category == _BOLETO_CATEGORIA,
            Document.file_name == nome_arquivo,
            Document.active.is_(True),
        )
        .first()
    )
    if ja_existe:
        stats.boletos_reused += 1
        return

    if dry_run:
        stats.boletos_created += 1
        return

    import requests

    from app.services.storage import upload_bytes

    try:
        resposta = requests.get(url, timeout=settings.sgr_timeout_seconds)
        resposta.raise_for_status()
        conteudo = resposta.content
    except requests.RequestException as exc:
        stats.boletos_skipped += 1
        stats.skip(f"boleto {mapped.get('external_id')}: falha ao baixar o PDF ({type(exc).__name__})")
        return

    if not conteudo.startswith(b'%PDF'):
        stats.boletos_skipped += 1
        stats.skip(f"boleto {mapped.get('external_id')}: o link não devolveu um PDF")
        return

    object_key = f'clients/{client_id}/documents/{uuid4()}-{nome_arquivo}'
    try:
        upload_bytes(object_name=object_key, content=conteudo, content_type='application/pdf')
    except Exception as exc:  # noqa: BLE001 — falha de storage não aborta a migração
        stats.boletos_skipped += 1
        stats.skip(f"boleto {mapped.get('external_id')}: falha ao gravar no storage ({type(exc).__name__})")
        return

    db.add(Document(
        file_name=nome_arquivo,
        object_key=object_key,
        content_type='application/pdf',
        size_bytes=len(conteudo),
        reference_type='client',
        reference_id=client_id,
        category=_BOLETO_CATEGORIA,
        active=True,
    ))
    db.flush()
    stats.boletos_created += 1


def _import_invoices(
    db: Session, node: ClientNode, client_id: int, stats: ImportStats, dry_run: bool,
) -> None:
    """Guarda o XML da NFS-e como documento do cliente.

    Não usa a tabela nfse_notas de propósito: lá `billing_id` é único (uma
    nota por cobrança), e no SGR uma nota cobre o boleto consolidado inteiro
    — que aqui virou várias cobranças, uma por placa. Escolher uma delas para
    receber a nota inventaria um vínculo que não existe.

    O arquivo é baixado de um gateway de terceiros (o SGR só devolve a URL),
    então cada download pode falhar sozinho sem derrubar a importação.

    Em `dry_run` nada é baixado nem enviado ao storage: o rollback desfaz as
    linhas do banco, mas não removeria o objeto já gravado no MinIO — a
    simulação deixaria lixo atrás de si.
    """
    if not node.invoices:
        return

    if dry_run:
        stats.invoices_created += len(node.invoices)
        return

    import requests  # local: só a importação de notas depende de rede aqui

    from app.services.storage import upload_bytes

    for nota in node.invoices:
        numero = nota.get('numero_nf')
        nome_arquivo = f'nfse-{numero}.xml'

        if not nota.get('url'):
            stats.invoices_skipped += 1
            stats.skip(f"nota fiscal {numero}: {nota.get('erro') or 'sem XML disponível'}")
            continue

        ja_existe = (
            db.query(Document)
            .filter(
                Document.reference_type == 'client',
                Document.reference_id == client_id,
                Document.category == _NFSE_CATEGORIA,
                Document.file_name == nome_arquivo,
                Document.active.is_(True),
            )
            .first()
        )
        if ja_existe:
            stats.invoices_reused += 1
            continue

        try:
            resposta = requests.get(nota['url'], timeout=settings.sgr_timeout_seconds)
            resposta.raise_for_status()
            conteudo = resposta.content
        except requests.RequestException as exc:
            stats.invoices_skipped += 1
            stats.skip(f'nota fiscal {numero}: falha ao baixar o XML ({type(exc).__name__})')
            continue

        if not conteudo:
            stats.invoices_skipped += 1
            stats.skip(f'nota fiscal {numero}: XML veio vazio')
            continue

        object_key = f'clients/{client_id}/documents/{uuid4()}-{nome_arquivo}'
        try:
            upload_bytes(object_name=object_key, content=conteudo, content_type='application/xml')
        except Exception as exc:  # noqa: BLE001 — falha de storage não pode abortar a migração
            stats.invoices_skipped += 1
            stats.skip(f'nota fiscal {numero}: falha ao gravar no storage ({type(exc).__name__})')
            continue

        db.add(Document(
            file_name=nome_arquivo,
            object_key=object_key,
            content_type='application/xml',
            size_bytes=len(conteudo),
            reference_type='client',
            reference_id=client_id,
            category=_NFSE_CATEGORIA,
            active=True,
        ))
        db.flush()
        stats.invoices_created += 1


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
        _import_billings(db, node, client.id, stats, dry_run)
        _import_invoices(db, node, client.id, stats, dry_run)

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return stats
