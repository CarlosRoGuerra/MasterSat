"""
Geração e consulta de boletos via API de Cobrança Ailos (v2/boletos).

Monta o payload a partir de ``Billing``/``Client``, chama
``app.services.ailos_client.request`` e persiste o resultado em
``ailos_boletos``/``ailos_lotes``.

Conformidade com a documentação (Manual v2, Postman oficial jan/26):
  - Geração em lote/carnê envia um objeto envelopado
    ``{"convenioCobranca": {...}, "boletos"|"carnes": [...]}`` (Postman v2),
    e não um array nu.
  - ``gerar_carne_lote`` usa ``tipoVencimento`` como objeto
    (``{tipoVencimento, quantidadeXDias, diaXDeCadaMes}``), com
    ``tipoVencimento=1`` (Mensal) — único modo suportado nesta entrega.
  - v2 não possui consulta de lote; o lote de BOLETO usa o v1
    (``/v1/.../consultar/boleto/lote``). O lote de CARNÊ NÃO usa o
    ``/consultar/carne/lote`` (404 no gateway do APIm) — recupera parcela por
    parcela pela consulta de boleto individual (``_consultar_carne_por_boleto``).
  - ``consultar_lote`` aceita tanto uma lista de itens quanto um dict com
    ``boletos``/``itens`` na resposta, casando cada item ao ``billing_id``
    via ``documento.numeroDocumento`` (enviado como ``billing.id``).
"""
from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ailos_api_log import AilosApiLog
from app.models.ailos_boleto import AilosBoleto
from app.models.ailos_lote import AilosLote
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.services import ailos_client
from app.services.ailos_validators import (
    AilosValidationError,
    normalize_text,
    only_digits,
    split_phone,
    validar_cpf_cnpj,
    validate_boleto_payload,
)
from app.services.boleto_ailos import DadosBoleto
from app.services.financial import (
    lock_billings_for_update,
    marcar_billing_pago,
    refresh_overdue_statuses,
)

# ---------------------------------------------------------------------------
# Caminhos (relativos a AILOS_GATEWAY_BASE_URL)
# ---------------------------------------------------------------------------
_PATH_GERAR_BOLETO = '/v2/boletos/gerar/boleto/convenios/{convenio}'
_PATH_GERAR_BOLETO_LOTE = '/v2/boletos/gerar/boleto/lote/convenios/{convenio}'
_PATH_GERAR_CARNE_LOTE = '/v2/boletos/gerar/carne/lote/convenios/{convenio}'
_PATH_CONSULTAR_BOLETO = '/v2/boletos/consultar/boleto/convenios/{convenio}/{numero_boleto}'
# v2 não possui consulta de lote — usa-se os endpoints v1 (Cartilha jan/26, p.9).
# Há paths distintos para boleto e carnê.
_PATH_CONSULTAR_LOTE = '/v1/boletos/consultar/boleto/lote/convenios/{convenio}/{ticket}'


# ---------------------------------------------------------------------------
# Mapeamento de payload
# ---------------------------------------------------------------------------

def montar_dados_pagador(client: Client) -> dict:
    """Monta o bloco ``pagador`` (entidadeLegal/telefone/emails/endereco/mensagemPagador) a partir de um Client."""
    cpf_cnpj = validar_cpf_cnpj(client.cpf_cnpj)
    tipo_pessoa = 1 if (client.type or 'pf').lower() == 'pf' else 2
    ddi, ddd, numero_telefone = split_phone(client.phone)

    emails = [{'endereco': client.email}] if client.email else []

    return {
        'entidadeLegal': {
            'identificadorReceitaFederal': cpf_cnpj,
            'tipoPessoa': tipo_pessoa,
            'nome': normalize_text(client.name, 50),
        },
        'telefone': {'ddi': ddi, 'ddd': ddd, 'numero': numero_telefone},
        'emails': emails,
        'endereco': {
            'cep': only_digits(client.zip_code),
            'logradouro': normalize_text(client.address_line, 56),
            'numero': client.address_number or '',
            'complemento': client.address_complement or '',
            'bairro': normalize_text(client.neighborhood, 30),
            'cidade': normalize_text(client.city, 30),
            'uf': (client.state or '').upper(),
        },
        'mensagemPagador': ['REFERENTE AO CONTRATO DE RASTREAMENTO'],
    }


def resolver_pagador(db: Session, billing: Billing, fallback: Client | None = None) -> Client:
    """Cliente que PAGA o boleto desta cobrança.

    Quando o contrato tem um interveniente financeiro, é ele quem responde pela
    cobrança — o boleto (pagador/sacado) sai no nome dele, não no do dono do
    veículo. Sem interveniente (ou se ele foi removido), cai no cliente da
    própria cobrança.
    """
    # Títulos novos guardam um snapshot explícito. Ele tem precedência sobre o
    # contrato atual para impedir que uma troca posterior de interveniente altere
    # retroativamente o pagador de boleto, recibo e NFS-e já emitidos.
    if getattr(billing, 'payer_client_id', None):
        payer = db.get(Client, billing.payer_client_id)
        if payer and not payer.is_deleted:
            return payer
        raise ValueError(
            f'Responsável financeiro #{billing.payer_client_id} da cobrança '
            f'#{billing.id} não está disponível.'
        )

    # Compatibilidade com títulos legados, anteriores ao snapshot do pagador.
    if billing.contract_id:
        contrato = db.get(Contract, billing.contract_id)
        if contrato and contrato.interveniente_client_id:
            interveniente = db.get(Client, contrato.interveniente_client_id)
            if interveniente and not interveniente.is_deleted:
                return interveniente
    return fallback or _obter_client(db, billing.client_id)


def _obter_client(db: Session, client_id: int) -> Client:
    c = db.get(Client, client_id)
    if c is None:
        raise ValueError(f'Cliente {client_id} não encontrado')
    return c


def montar_payload_boleto(billing: Billing, client: Client) -> dict:
    """Monta o payload de geração de boleto a partir de Billing + Client."""
    payload = {
        'convenioCobranca': {'codigoCarteiraCobranca': settings.ailos_default_carteira},
        'documento': {
            'numeroDocumento': billing.id,
            'descricaoDocumento': normalize_text(billing.title or 'MENSALIDADE', 15),
            'especieDocumento': 2,
        },
        'emissao': {
            'formaEmissao': settings.ailos_default_forma_emissao,
            'dataEmissaoDocumento': date.today().isoformat(),
        },
        'pagador': montar_dados_pagador(client),
        'vencimento': {'dataVencimento': billing.due_date.isoformat()},
        'instrucoes': {
            'valorAbatimento': 0,
            'tipoDesconto': 3,
            'descontos': [],
            'tipoMulta': 3,
            'valorMulta': 0,
            'tipoJurosMora': 3,
            'valorJurosMora': 0,
            'diasNegativacao': 0,
            'diasProtesto': 0,
        },
        'valorBoleto': {'valorNominal': float(billing.amount)},
        'avisoSms': {
            'enviarAvisoVencimentoSms': 0,
            'enviarAvisoVencimentoSmsAntesVencimento': False,
            'enviarAvisoVencimentoSmsDiaVencimento': False,
            'enviarAvisoVencimentoSmsAposVencimento': False,
        },
        'pagamentoDivergente': {
            'tipoPagamentoDivergente': 0,
            'valorMinimoPagamentoDivergente': 0,
        },
        'indicadorRegistroNuclea': settings.ailos_default_indicador_registro_nuclea,
    }

    # BolePix: boleto híbrido com QR Code Pix (V2). Só envia quando habilitado
    # via AILOS_BOLE_PIX e a conta tiver chave Pix aleatória vinculada na Ailos.
    if settings.ailos_bole_pix:
        payload['bolePix'] = True

    validate_boleto_payload(payload)
    return payload


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _to_str_or_none(value) -> str | None:
    if value is None:
        return None
    return str(value)


def _extrair_pix(pix) -> tuple[str | None, str | None]:
    """Extrai ``(emv, imagem_base64)`` do bloco ``pix`` do BolePix.

    Detecta por CONTEÚDO (não por nome de campo, que varia entre versões):
      - EMV / "copia e cola" (BR Code): string que começa com ``000201``.
      - Imagem do QR: PNG/JPEG em base64 (``iVBOR...`` / ``/9j/...``) ou data URI.

    A Ailos devolve ``"pix": null`` quando a conta não tem a chave Pix vinculada
    à funcionalidade — nesse caso retorna ``(None, None)``.
    """
    emv: str | None = None
    imagem: str | None = None
    if isinstance(pix, dict):
        for value in pix.values():
            if not isinstance(value, str) or not value.strip():
                continue
            s = value.strip()
            if s.startswith('000201'):
                emv = s
            elif s.startswith('iVBOR') or s.startswith('/9j/') or s.startswith('data:image'):
                imagem = s
    return emv, imagem


def _upsert_ailos_boleto(
    db: Session,
    billing_id: int,
    payload_request: dict,
    payload_response: dict | None,
    lote_id: int | None = None,
    *,
    commit: bool = True,
) -> AilosBoleto:
    def _aplicar(alvo: AilosBoleto) -> None:
        if lote_id is not None:
            alvo.lote_id = lote_id

        alvo.payload_request = payload_request

        if payload_response is not None:
            alvo.payload_response = payload_response
            # A geração V2 responde envelopando em {"boleto": {...}}; consultas
            # podem vir já no nível raiz. Desembrulha para ler os dois formatos.
            dados = payload_response.get('boleto')
            if not isinstance(dados, dict):
                dados = payload_response

            documento = dados.get('documento') or {}
            codigo_barras_obj = dados.get('codigoBarras') or {}
            valor_boleto = dados.get('valorBoleto') or {}
            vencimento = dados.get('vencimento') or {}

            alvo.numero_documento = _to_str_or_none(documento.get('numeroDocumento'))
            alvo.nosso_numero = _to_str_or_none(documento.get('nossoNumero'))
            alvo.identificador_unico_titulo = _to_str_or_none(documento.get('identificadorUnicoTitulo'))
            alvo.linha_digitavel = codigo_barras_obj.get('linhaDigitavel')
            alvo.codigo_barras = codigo_barras_obj.get('codigoBarras')
            alvo.status_ailos = _to_str_or_none(dados.get('indicadorSituacaoBoleto'))
            alvo.pix_emv, alvo.pix_qr_base64 = _extrair_pix(dados.get('pix'))

            # valorNominal (consulta) ou valorOriginalTitulo/valorAtual (geração)
            valor_nominal = (
                valor_boleto.get('valorNominal')
                if valor_boleto.get('valorNominal') is not None
                else valor_boleto.get('valorOriginalTitulo') or valor_boleto.get('valorAtual')
            )
            if valor_nominal is not None:
                alvo.valor_nominal = Decimal(str(valor_nominal))

            # Aceita dataVencimento (consulta) ou dataVencimentoAtual (geração),
            # que pode vir como datetime ISO ("2026-06-26T00:00:00").
            data_venc = (
                vencimento.get('dataVencimento')
                or vencimento.get('dataVencimentoAtual')
                or vencimento.get('dataVencimentoOriginal')
            )
            if data_venc:
                try:
                    alvo.data_vencimento = date.fromisoformat(str(data_venc)[:10])
                except ValueError:
                    pass

    boleto = db.query(AilosBoleto).filter_by(billing_id=billing_id).first()
    if boleto is None:
        try:
            # `db.add` acontece DENTRO do savepoint: `begin_nested()` flusha
            # qualquer pendência da Session para tirar o "snapshot" antes de
            # abrir o SAVEPOINT — se o `add` viesse antes, esse flush inicial
            # já dispararia o INSERT (e um eventual IntegrityError) fora de
            # qualquer proteção de savepoint.
            with db.begin_nested():
                boleto = AilosBoleto(billing_id=billing_id, numero_convenio=settings.ailos_numero_convenio)
                db.add(boleto)
                _aplicar(boleto)
                db.flush()
        except IntegrityError:
            # Corrida: outra sessão inseriu este billing_id entre o SELECT e
            # este INSERT (billing_id é UNIQUE — ver app/models/ailos_boleto.py).
            # O ROLLBACK TO SAVEPOINT já descartou a tentativa; reaplica os
            # mesmos dados sobre o registro que venceu, em vez de propagar o erro.
            boleto = db.query(AilosBoleto).filter_by(billing_id=billing_id).first()
            _aplicar(boleto)
            db.flush()
    else:
        _aplicar(boleto)
        db.flush()

    if commit:
        db.commit()
        db.refresh(boleto)
    return boleto


_AILOS_REGISTRATION_IN_PROGRESS = {'REGISTRANDO', 'PROCESSANDO'}


def _lock_open_billings_for_ailos(
    db: Session,
    billings: list[Billing],
) -> list[Billing]:
    """Serializa somente os Billing que serão enviados ao banco.

    A revalidação ocorre depois do ``FOR UPDATE`` para impedir que uma baixa,
    um cancelamento ou uma unificação confirmada em paralelo gere um título
    bancário a partir de estado obsoleto.
    """
    ids = [billing.id for billing in billings]
    locked_by_id = {billing.id: billing for billing in lock_billings_for_update(db, ids)}
    missing = [billing_id for billing_id in ids if billing_id not in locked_by_id]
    unavailable = [
        billing_id
        for billing_id in ids
        if billing_id in locked_by_id
        and (
            locked_by_id[billing_id].is_deleted
            or locked_by_id[billing_id].status
            not in (BillingStatus.PENDING, BillingStatus.OVERDUE)
        )
    ]
    if missing or unavailable:
        invalid = sorted(set(missing + unavailable))
        raise AilosValidationError([
            f'Cobranças indisponíveis para registro na Ailos: {invalid}'
        ])
    return [locked_by_id[billing_id] for billing_id in ids]


def _reserve_ailos_billings(
    db: Session,
    billings_and_payloads: list[tuple[Billing, dict]],
    *,
    allow_in_progress_ids: set[int] | None = None,
) -> list[AilosBoleto]:
    """Persiste a intenção idempotente antes de chamar o serviço externo.

    O lock do Billing protege a decisão; a constraint 1:1 de ``billing_id``
    protege a reserva. Depois do commit, mutações financeiras conseguem ver
    ``REGISTRANDO`` e não alteram valor/estado enquanto a Ailos responde.
    """
    billing_ids = [billing.id for billing, _ in billings_and_payloads]
    allowed_takeovers = allow_in_progress_ids or set()
    existing_by_id = {
        boleto.billing_id: boleto
        for boleto in db.query(AilosBoleto)
        .filter(AilosBoleto.billing_id.in_(billing_ids))
        .all()
    }
    conflicts = [
        billing_id
        for billing_id, boleto in existing_by_id.items()
        if boleto.linha_digitavel
        or boleto.codigo_barras
        or (
            boleto.status_ailos in _AILOS_REGISTRATION_IN_PROGRESS
            and billing_id not in allowed_takeovers
        )
    ]
    if conflicts:
        raise AilosValidationError([
            'Cobranças já registradas ou com registro Ailos em andamento: '
            f'{sorted(conflicts)}'
        ])

    reservations: list[AilosBoleto] = []
    for billing, payload in billings_and_payloads:
        boleto = existing_by_id.get(billing.id)
        if boleto is None:
            boleto = AilosBoleto(
                billing_id=billing.id,
                numero_convenio=settings.ailos_numero_convenio,
            )
            db.add(boleto)
        boleto.numero_convenio = settings.ailos_numero_convenio
        boleto.payload_request = payload
        boleto.payload_response = None
        boleto.status_ailos = 'REGISTRANDO'
        reservations.append(boleto)
    db.commit()
    return reservations


def _mark_ailos_registration_error(
    db: Session,
    reservations: list[AilosBoleto],
) -> None:
    for reservation in reservations:
        reservation.status_ailos = 'ERRO_REGISTRO'
    db.commit()


# ---------------------------------------------------------------------------
# Geração — boleto único / lote / carnê
# ---------------------------------------------------------------------------

def gerar_boleto(
    db: Session,
    billing: Billing,
    client: Client,
    *,
    manual_retry: bool = False,
) -> AilosBoleto:
    """Gera um boleto via API Ailos para um billing.

    Idempotente: se já existe um boleto registrado (com linha digitável) para
    este billing, retorna o existente sem chamar a Ailos de novo — evita erro
    de número duplicado ao re-clicar "Gerar boleto" na tela.
    """
    billing = _lock_open_billings_for_ailos(db, [billing])[0]
    existing = db.query(AilosBoleto).filter_by(billing_id=billing.id).first()
    if existing is not None and existing.linha_digitavel:
        return existing

    payload = montar_payload_boleto(billing, resolver_pagador(db, billing, client))
    reservations = _reserve_ailos_billings(
        db,
        [(billing, payload)],
        allow_in_progress_ids={billing.id} if manual_retry else None,
    )

    try:
        resp = ailos_client.request(
            db, 'POST',
            _PATH_GERAR_BOLETO.format(convenio=settings.ailos_numero_convenio),
            json_body=payload,
            billing_id=billing.id,
        )
    except ailos_client.AilosApiError as exc:
        # "Boleto já cadastrado": a Ailos já tem este título (uma tentativa
        # anterior registrou lá mas não persistiu localmente). Em vez de falhar
        # e deixar o cliente sem boleto pagável, recupera os dados oficiais
        # consultando pelo numeroDocumento (= billing.id).
        msg = (exc.ailos_message or '').lower()
        if exc.status_code == 400 and ('cadastrad' in msg or 'já existe' in msg or 'ja existe' in msg):
            recuperado = _recuperar_boleto_existente(db, billing, payload)
            if recuperado is not None and recuperado.linha_digitavel:
                return recuperado
        _mark_ailos_registration_error(db, reservations)
        raise
    except Exception:
        _mark_ailos_registration_error(db, reservations)
        raise

    response_body = resp.json if isinstance(resp.json, dict) else {}
    return _upsert_ailos_boleto(db, billing.id, payload, response_body)


def _recuperar_boleto_existente(db: Session, billing: Billing, payload: dict) -> AilosBoleto | None:
    """Recupera um boleto que já existe na Ailos mas não localmente,
    consultando pelo numeroDocumento (= billing.id) e persistindo os dados
    oficiais. Retorna None se a consulta não trouxer um boleto utilizável."""
    try:
        resp = consultar_boleto(db, str(billing.id))
    except (ailos_client.AilosError, ailos_client.AilosApiError):
        return None
    dados = resp
    if isinstance(resp, list):
        dados = resp[0] if resp else None
    if not isinstance(dados, dict):
        return None
    return _upsert_ailos_boleto(db, billing.id, payload, dados)


def gerar_boleto_lote(db: Session, billings: list[Billing], clients_by_id: dict[int, Client]) -> AilosLote:
    """Gera um lote assíncrono de boletos. Retorna o ``AilosLote`` (status='processing')."""
    billings = _lock_open_billings_for_ailos(db, billings)
    payloads = [
        montar_payload_boleto(b, resolver_pagador(db, b, clients_by_id.get(b.client_id)))
        for b in billings
    ]
    reservations = _reserve_ailos_billings(db, list(zip(billings, payloads)))

    body_lote = {
        'convenioCobranca': {'codigoCarteiraCobranca': settings.ailos_default_carteira},
        'boletos': payloads,
    }

    try:
        resp = ailos_client.request(
            db, 'POST',
            _PATH_GERAR_BOLETO_LOTE.format(convenio=settings.ailos_numero_convenio),
            json_body=body_lote,
        )
    except Exception:
        _mark_ailos_registration_error(db, reservations)
        raise

    body = resp.json if isinstance(resp.json, dict) else {}
    ticket = body.get('ticketLote') or body.get('ticket')

    lote = AilosLote(
        tipo='boleto',
        ticket=ticket,
        numero_convenio=settings.ailos_numero_convenio,
        billing_ids=[b.id for b in billings],
        status='processing',
    )
    db.add(lote)
    db.flush()
    for boleto in reservations:
        boleto.lote_id = lote.id
        boleto.status_ailos = 'PROCESSANDO'
    db.commit()
    db.refresh(lote)

    return lote


def gerar_carne_lote(
    db: Session,
    billings_by_parcela: list[tuple[int, Billing]],
    clients_by_id: dict[int, Client],
) -> AilosLote:
    """
    Gera um carnê (lote de parcelas) via API Ailos.

    ``billings_by_parcela`` é uma lista de ``(numeroParcela, billing)`` na
    ordem das parcelas do carnê.
    """
    original_numbers = [numero_parcela for numero_parcela, _ in billings_by_parcela]
    locked = _lock_open_billings_for_ailos(
        db, [billing for _, billing in billings_by_parcela],
    )
    billings_by_parcela = list(zip(original_numbers, locked))
    payloads = []
    billings = []
    for numero_parcela, b in billings_by_parcela:
        payload = montar_payload_boleto(b, resolver_pagador(db, b, clients_by_id.get(b.client_id)))
        payload['numeroParcela'] = numero_parcela
        # tipoVencimento é um objeto na v2 (Manual p.39 / Postman v2).
        # tipoVencimento: 1=Mensal, 2=A cada x dias, 3=Dia x de cada mês.
        payload['tipoVencimento'] = {
            'tipoVencimento': 1,
            'quantidadeXDias': 0,
            'diaXDeCadaMes': 0,
        }
        payloads.append(payload)
        billings.append(b)
    reservations = _reserve_ailos_billings(db, list(zip(billings, payloads)))

    body_lote = {
        'convenioCobranca': {'codigoCarteiraCobranca': settings.ailos_default_carteira},
        'carnes': payloads,
    }

    try:
        resp = ailos_client.request(
            db, 'POST',
            _PATH_GERAR_CARNE_LOTE.format(convenio=settings.ailos_numero_convenio),
            json_body=body_lote,
        )
    except Exception:
        _mark_ailos_registration_error(db, reservations)
        raise

    body = resp.json if isinstance(resp.json, dict) else {}
    ticket = body.get('ticketLote') or body.get('ticket')

    lote = AilosLote(
        tipo='carne',
        ticket=ticket,
        numero_convenio=settings.ailos_numero_convenio,
        billing_ids=[b.id for b in billings],
        status='processing',
    )
    db.add(lote)
    db.flush()
    for boleto in reservations:
        boleto.lote_id = lote.id
        boleto.status_ailos = 'PROCESSANDO'
    db.commit()
    db.refresh(lote)

    return lote


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------

def consultar_boleto(db: Session, numero_boleto: str) -> dict | list | None:
    """Consulta um boleto já gerado pelo número/identificador retornado pela Ailos."""
    resp = ailos_client.request(
        db, 'GET',
        _PATH_CONSULTAR_BOLETO.format(convenio=settings.ailos_numero_convenio, numero_boleto=numero_boleto),
    )
    return resp.json


CARNE_PRAZO_ESPERA = timedelta(minutes=10)


def carne_prazo_esgotado(lote: AilosLote) -> bool:
    """True quando já passou tempo suficiente desde a criação do lote para
    desistir de esperar as parcelas que ainda não confirmaram (usado tanto
    para fechar o status do lote quanto para decidir se o PDF pode sair
    parcial em vez de recusar o download)."""
    criado_em = lote.created_at
    if criado_em.tzinfo is None:
        criado_em = criado_em.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - criado_em) > CARNE_PRAZO_ESPERA


def _consultar_carne_por_boleto(db: Session, lote: AilosLote) -> dict:
    """
    Recupera as parcelas de um carnê consultando cada boleto individualmente
    pelo numeroDocumento (= billing.id).

    Motivo: o endpoint de consulta de LOTE de carnê da Ailos devolve 404 no
    gateway do APIM ("No matching resource found") — o recurso não está
    publicado, apesar de constar no Postman. Já a consulta de boleto individual
    (/v2/.../consultar/boleto/...) funciona; é a mesma usada na recuperação do
    boleto avulso.

    Só marca o lote 'completed' quando TODAS as parcelas já têm os dados
    oficiais — não a primeira que resolver. Antes, o lote fechava assim que UMA
    parcela ficava pronta; o frontend via 'completed' e baixava o PDF na hora,
    que saía com só essa parcela (o carnê "gerava 1 boleto só"). Timeout
    defensivo de 10 min: se uma parcela específica nunca resolver (falha
    pontual do lado do banco), o lote fecha mesmo assim com o que conseguiu —
    senão ficaria 'processing' para sempre.
    """
    total = len(lote.billing_ids or [])
    resolvidos = 0
    for billing_id in (lote.billing_ids or []):
        boleto = db.query(AilosBoleto).filter_by(billing_id=billing_id).first()
        if boleto is not None and boleto.linha_digitavel:
            resolvidos += 1
            continue
        try:
            dados = consultar_boleto(db, str(billing_id))
        except (ailos_client.AilosError, ailos_client.AilosApiError):
            continue
        if isinstance(dados, list):
            dados = dados[0] if dados else None
        if not isinstance(dados, dict):
            continue
        payload_request = boleto.payload_request if (boleto and boleto.payload_request) else {}
        atualizado = _upsert_ailos_boleto(db, billing_id, payload_request, dados, lote_id=lote.id)
        if atualizado.linha_digitavel:
            resolvidos += 1

    if resolvidos == 0:
        return {'status': 'processing'}

    if resolvidos < total and not carne_prazo_esgotado(lote):
        return {'status': 'processing'}

    lote.status = 'completed'
    db.commit()
    db.refresh(lote)
    return {'status': 'completed', 'lote': lote}


def parcelas_do_lote(db: Session, lote: AilosLote) -> list[dict]:
    """Detalhe por parcela de um lote/carnê — status individual, não só o
    agregado 'X de Y'. Usado pela tela de acompanhamento para montar a tabela
    com uma linha por parcela e ação de retry quando aplicável.

    'erro' só é atribuído a partir de uma tentativa de REGISTRO malsucedida
    (POST gerar/boleto — o mesmo path usado por ``gerar_boleto``/retry
    individual), nunca de uma consulta: durante o processamento normal do
    lote, cada parcela ainda não confirmada 404 na consulta individual até a
    Ailos terminar de processá-la, e isso não é um erro — é só cedo demais
    para saber. Sem uma tentativa de registro que tenha de fato falhado, a
    parcela fica 'processando' (o frontend usa o prazo de 10 min do lote para
    sugerir "não localizado, gerar manualmente").
    """
    billing_ids = lote.billing_ids or []
    boletos_by_billing = {
        b.billing_id: b
        for b in db.query(AilosBoleto).filter(AilosBoleto.billing_id.in_(billing_ids)).all()
    } if billing_ids else {}
    billings_by_id = {
        b.id: b
        for b in db.query(Billing).filter(Billing.id.in_(billing_ids)).all()
    } if billing_ids else {}

    resultado = []
    for numero_parcela, billing_id in enumerate(billing_ids, start=1):
        boleto = boletos_by_billing.get(billing_id)
        billing = billings_by_id.get(billing_id)
        status = 'processando'
        erro = None
        if boleto is not None and boleto.linha_digitavel:
            status = 'registrado'
        else:
            ultima_tentativa = (
                db.query(AilosApiLog)
                .filter(AilosApiLog.billing_id == billing_id, AilosApiLog.endpoint.contains('gerar/boleto'))
                .order_by(AilosApiLog.id.desc())
                .first()
            )
            if ultima_tentativa is not None and not ultima_tentativa.success:
                status = 'erro'
                erro = ultima_tentativa.error_message
        resultado.append({
            'billing_id': billing_id,
            'numero_parcela': numero_parcela,
            'vencimento': billing.due_date if billing else None,
            'valor': float(billing.amount) if billing else None,
            'status': status,
            'nosso_numero': boleto.nosso_numero if boleto else None,
            'linha_digitavel': boleto.linha_digitavel if boleto else None,
            'erro': erro,
        })
    return resultado


def _mensagem_erro(exc: Exception) -> str:
    """Mensagem legível de uma falha de registro — prioriza a mensagem real
    devolvida pela Ailos (``AilosApiError.ailos_message``) sobre o texto
    genérico de ``str(exc)``/``friendly_message`` (ex.: "HTTP 422" sem
    nenhum contexto do que de fato foi rejeitado)."""
    if isinstance(exc, ailos_client.AilosApiError):
        return exc.ailos_message or exc.friendly_message or str(exc)
    return str(exc)


def registrar_parcela_individual(db: Session, lote: AilosLote, billing_id: int) -> AilosBoleto:
    """Tenta registrar (ou recuperar) UMA parcela específica de um lote/carnê.

    Reaproveita ``gerar_boleto`` — mesma idempotência (não rechama a Ailos se
    já tem linha digitável; recupera pelo numeroDocumento se a Ailos disser
    "já cadastrado"), e nunca cria um Billing novo, só registra o que já
    existe. Mantém a associação ao lote original mesmo quando o registro é
    feito por aqui em vez do envio em lote inicial.
    """
    if billing_id not in (lote.billing_ids or []):
        raise ValueError(f'A cobrança {billing_id} não pertence a este lote.')
    billing = db.get(Billing, billing_id)
    if billing is None or billing.is_deleted:
        raise ValueError(f'Cobrança {billing_id} não encontrada.')

    boleto = gerar_boleto(
        db,
        billing,
        resolver_pagador(db, billing),
        manual_retry=True,
    )
    if boleto.lote_id != lote.id:
        boleto.lote_id = lote.id
        db.commit()
        db.refresh(boleto)
    return boleto


def registrar_pendentes_do_lote(db: Session, lote: AilosLote) -> dict:
    """"Gerar boletos pendentes": tenta registrar TODAS as parcelas do lote
    que ainda não confirmaram, uma a uma. Uma falha pontual não interrompe as
    demais — o objetivo é avançar o máximo possível numa única ação.

    Fecha o lote como 'completed' se, ao final, todas as parcelas resolverem.
    """
    sucesso: list[int] = []
    falhas: list[dict] = []
    for billing_id in (lote.billing_ids or []):
        boleto_atual = db.query(AilosBoleto).filter_by(billing_id=billing_id).first()
        if boleto_atual is not None and boleto_atual.linha_digitavel:
            continue
        try:
            registrar_parcela_individual(db, lote, billing_id)
            sucesso.append(billing_id)
        except (ailos_client.AilosError, ailos_client.AilosApiError, AilosValidationError, ValueError) as exc:
            falhas.append({'billing_id': billing_id, 'erro': _mensagem_erro(exc)})

    boletos = db.query(AilosBoleto).filter_by(lote_id=lote.id).all()
    prontas = sum(1 for b in boletos if b.linha_digitavel)
    if prontas == len(lote.billing_ids or []):
        lote.status = 'completed'
        db.commit()

    return {'sucesso': sucesso, 'falhas': falhas, 'parcelas': parcelas_do_lote(db, lote)}


def consultar_lote(db: Session, lote: AilosLote) -> dict:
    """
    Consulta o status de um lote/carnê pelo ``ticket``.

    Enquanto a Ailos ainda estiver processando, retorna
    ``{'status': 'processing'}`` sem alterar ``lote.status``. Quando
    concluído, atualiza ``lote.status='completed'``, ``payload_response`` e
    faz upsert em ``ailos_boletos`` para cada item retornado.

    Carnê é tratado à parte: a consulta de lote de carnê da Ailos devolve 404
    no gateway, então recupera parcela por parcela (ver
    ``_consultar_carne_por_boleto``).
    """
    if lote.tipo == 'carne':
        return _consultar_carne_por_boleto(db, lote)

    resp = ailos_client.request(
        db, 'GET',
        _PATH_CONSULTAR_LOTE.format(convenio=settings.ailos_numero_convenio, ticket=lote.ticket),
    )

    if resp.processing:
        return {'status': 'processing'}

    body = resp.json
    lote.status = 'completed'
    lote.payload_response = body if isinstance(body, dict) else {'itens': body}
    db.commit()

    if isinstance(body, list):
        itens = body
    elif isinstance(body, dict):
        itens = body.get('boletos') or body.get('itens') or []
    else:
        itens = []

    itens_by_billing: dict[int, dict] = {}
    for item in itens:
        documento = item.get('documento') or item
        numero_documento = documento.get('numeroDocumento')
        try:
            billing_id = int(numero_documento)
        except (TypeError, ValueError):
            continue
        itens_by_billing[billing_id] = item

    for billing_id in lote.billing_ids:
        item = itens_by_billing.get(billing_id)
        if item is None:
            continue
        boleto = db.query(AilosBoleto).filter_by(billing_id=billing_id).first()
        payload_request = boleto.payload_request if boleto else {}
        _upsert_ailos_boleto(db, billing_id, payload_request, item, lote_id=lote.id)

    db.refresh(lote)
    return {'status': 'completed', 'lote': lote}


# ---------------------------------------------------------------------------
# Override de dados oficiais no PDF/JSON do boleto CNAB existente
# ---------------------------------------------------------------------------

def aplicar_dados_oficiais_ailos(dados: DadosBoleto, ailos_boleto: AilosBoleto | None) -> DadosBoleto:
    """
    Substitui código de barras/linha digitável/nosso número calculados
    localmente pelos valores oficiais retornados pela API Ailos, se
    disponíveis. Sem ``ailos_boleto`` (ou sem dados oficiais ainda), retorna
    ``dados`` inalterado — zero mudança de comportamento para o fluxo CNAB
    atual.
    """
    if ailos_boleto is None or not (ailos_boleto.linha_digitavel and ailos_boleto.codigo_barras):
        return dados

    return dataclasses.replace(
        dados,
        codigo_barras=ailos_boleto.codigo_barras,
        linha_digitavel=ailos_boleto.linha_digitavel,
        nosso_numero_display=ailos_boleto.nosso_numero or dados.nosso_numero_display,
        pix_emv=ailos_boleto.pix_emv or dados.pix_emv,
        pix_qr_base64=ailos_boleto.pix_qr_base64 or dados.pix_qr_base64,
    )


# ---------------------------------------------------------------------------
# Conciliação de pagamento (baixa automática)
# ---------------------------------------------------------------------------

def _extrair_pagamento(payload_response: dict | None) -> dict:
    """Lê a situação de pagamento do retorno de consulta de boleto.

    Considera PAGO quando há valor pago (> 0) ou uma data de pagamento real
    (diferente de 0001-01-01). Desembrulha o envelope {"boleto": {...}}.
    """
    if not isinstance(payload_response, dict):
        return {'pago': False, 'valor_pago': None, 'data_pagamento': None}
    dados = payload_response.get('boleto')
    if not isinstance(dados, dict):
        dados = payload_response

    pagamento = dados.get('pagamento') or {}
    valor_boleto = dados.get('valorBoleto') or {}

    try:
        valor_pago = float(valor_boleto.get('valorPago') or 0)
    except (TypeError, ValueError):
        valor_pago = 0.0

    data_pagamento = None
    data_str = pagamento.get('dataPagamento')
    if data_str and not str(data_str).startswith('0001'):
        try:
            data_pagamento = date.fromisoformat(str(data_str)[:10])
        except ValueError:
            data_pagamento = None

    pago = valor_pago > 0 or data_pagamento is not None
    return {
        'pago': pago,
        'valor_pago': valor_pago if valor_pago else None,
        'data_pagamento': data_pagamento,
    }


def verificar_pagamento(db: Session, billing: Billing) -> dict:
    """Consulta o boleto na Ailos e, se pago, dá baixa na cobrança.

    Retorna ``{consultado, pago, data_pagamento, valor_pago, mensagem}``.
    """
    boleto = db.query(AilosBoleto).filter_by(billing_id=billing.id).first()
    if boleto is None or not boleto.nosso_numero:
        return {'consultado': False, 'pago': False, 'data_pagamento': None,
                'valor_pago': None, 'mensagem': 'Boleto Ailos ainda não gerado para esta cobrança.'}

    # A consulta usa o NÚMERO DO DOCUMENTO (= billing.id), não o nosso_numero
    # completo — a Ailos rejeita o nosso_numero com prefixo da conta
    # ("Validações"). Confirmado contra a API real.
    numero_consulta = boleto.numero_documento or str(billing.id)
    resp = consultar_boleto(db, numero_consulta)
    if isinstance(resp, dict):
        _upsert_ailos_boleto(db, billing.id, boleto.payload_request or {}, resp)

    info = _extrair_pagamento(resp if isinstance(resp, dict) else None)
    if not info['pago']:
        return {'consultado': True, 'pago': False, 'data_pagamento': None,
                'valor_pago': None, 'mensagem': 'Boleto ainda não consta como pago na Ailos.'}

    if billing.status != BillingStatus.PAID:
        marcar_billing_pago(
            db, billing,
            payment_date=info['data_pagamento'] or date.today(),
            paid_amount=info['valor_pago'] or float(billing.amount),
            payment_method='boleto',
            notes='Baixa automática via Ailos.',
        )
    return {'consultado': True, 'pago': True, 'data_pagamento': info['data_pagamento'],
            'valor_pago': info['valor_pago'], 'mensagem': 'Pagamento confirmado — baixa realizada.'}


def conciliar_boletos_abertos(db: Session, limit: int = 300) -> dict:
    """Consulta os boletos de cobranças em aberto e dá baixa nas pagas.

    Usado pela conciliação automática em background. Erro pontual num boleto
    não interrompe o lote. Retorna ``{consultados, baixados}``.
    """
    refresh_overdue_statuses(db)
    rows = (
        db.query(Billing)
        .join(AilosBoleto, AilosBoleto.billing_id == Billing.id)
        .filter(
            Billing.is_deleted.is_(False),
            Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
            AilosBoleto.nosso_numero.isnot(None),
        )
        .limit(limit)
        .all()
    )
    consultados = 0
    baixados = 0
    for billing in rows:
        try:
            res = verificar_pagamento(db, billing)
            if res.get('consultado'):
                consultados += 1
            if res.get('pago'):
                baixados += 1
        except (ailos_client.AilosError, ailos_client.AilosApiError):
            continue
    return {'consultados': consultados, 'baixados': baixados}
