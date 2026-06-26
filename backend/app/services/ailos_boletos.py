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
  - v2 não possui consulta de lote; usam-se os endpoints v1
    (``/v1/.../consultar/boleto/lote`` e ``/v1/.../consultar/carne/lote``),
    escolhidos conforme ``lote.tipo``.
  - ``consultar_lote`` aceita tanto uma lista de itens quanto um dict com
    ``boletos``/``itens`` na resposta, casando cada item ao ``billing_id``
    via ``documento.numeroDocumento`` (enviado como ``billing.id``).
"""
from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ailos_boleto import AilosBoleto
from app.models.ailos_lote import AilosLote
from app.models.billing import Billing
from app.models.client import Client
from app.services import ailos_client
from app.services.ailos_validators import (
    normalize_text,
    only_digits,
    split_phone,
    validar_cpf_cnpj,
    validate_boleto_payload,
)
from app.services.boleto_ailos import DadosBoleto

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
_PATH_CONSULTAR_CARNE_LOTE = '/v1/boletos/consultar/carne/lote/convenios/{convenio}/{ticket}'


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


def _extrair_pix_emv(pix) -> str | None:
    """Extrai o 'copia e cola' (EMV) do bloco ``pix`` do BolePix.

    A Ailos devolve ``"pix": null`` quando a conta ainda não tem a chave Pix
    vinculada à funcionalidade. Quando preenchido, o EMV vem em uma destas
    chaves (varia conforme a versão) — pega a primeira string não-vazia.
    """
    if not isinstance(pix, dict):
        return None
    for key in ('emv', 'textoQrCode', 'qrCode', 'qrcode', 'copiaECola',
                'pixCopiaECola', 'codigoQrCode', 'codigoPix', 'brCode'):
        value = pix.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _upsert_ailos_boleto(
    db: Session,
    billing_id: int,
    payload_request: dict,
    payload_response: dict | None,
    lote_id: int | None = None,
) -> AilosBoleto:
    boleto = db.query(AilosBoleto).filter_by(billing_id=billing_id).first()
    if boleto is None:
        boleto = AilosBoleto(billing_id=billing_id, numero_convenio=settings.ailos_numero_convenio)
        db.add(boleto)

    if lote_id is not None:
        boleto.lote_id = lote_id

    boleto.payload_request = payload_request

    if payload_response is not None:
        boleto.payload_response = payload_response
        # A geração V2 responde envelopando em {"boleto": {...}}; consultas
        # podem vir já no nível raiz. Desembrulha para ler os dois formatos.
        dados = payload_response.get('boleto')
        if not isinstance(dados, dict):
            dados = payload_response

        documento = dados.get('documento') or {}
        codigo_barras_obj = dados.get('codigoBarras') or {}
        valor_boleto = dados.get('valorBoleto') or {}
        vencimento = dados.get('vencimento') or {}

        boleto.numero_documento = _to_str_or_none(documento.get('numeroDocumento'))
        boleto.nosso_numero = _to_str_or_none(documento.get('nossoNumero'))
        boleto.identificador_unico_titulo = _to_str_or_none(documento.get('identificadorUnicoTitulo'))
        boleto.linha_digitavel = codigo_barras_obj.get('linhaDigitavel')
        boleto.codigo_barras = codigo_barras_obj.get('codigoBarras')
        boleto.status_ailos = _to_str_or_none(dados.get('indicadorSituacaoBoleto'))
        boleto.pix_emv = _extrair_pix_emv(dados.get('pix'))

        # valorNominal (consulta) ou valorOriginalTitulo/valorAtual (geração)
        valor_nominal = (
            valor_boleto.get('valorNominal')
            if valor_boleto.get('valorNominal') is not None
            else valor_boleto.get('valorOriginalTitulo') or valor_boleto.get('valorAtual')
        )
        if valor_nominal is not None:
            boleto.valor_nominal = Decimal(str(valor_nominal))

        # Aceita dataVencimento (consulta) ou dataVencimentoAtual (geração),
        # que pode vir como datetime ISO ("2026-06-26T00:00:00").
        data_venc = (
            vencimento.get('dataVencimento')
            or vencimento.get('dataVencimentoAtual')
            or vencimento.get('dataVencimentoOriginal')
        )
        if data_venc:
            try:
                boleto.data_vencimento = date.fromisoformat(str(data_venc)[:10])
            except ValueError:
                pass

    db.commit()
    db.refresh(boleto)
    return boleto


# ---------------------------------------------------------------------------
# Geração — boleto único / lote / carnê
# ---------------------------------------------------------------------------

def gerar_boleto(db: Session, billing: Billing, client: Client) -> AilosBoleto:
    """Gera um boleto via API Ailos para um billing.

    Idempotente: se já existe um boleto registrado (com linha digitável) para
    este billing, retorna o existente sem chamar a Ailos de novo — evita erro
    de número duplicado ao re-clicar "Gerar boleto" na tela.
    """
    existing = db.query(AilosBoleto).filter_by(billing_id=billing.id).first()
    if existing is not None and existing.linha_digitavel:
        return existing

    payload = montar_payload_boleto(billing, client)

    resp = ailos_client.request(
        db, 'POST',
        _PATH_GERAR_BOLETO.format(convenio=settings.ailos_numero_convenio),
        json_body=payload,
        billing_id=billing.id,
    )

    response_body = resp.json if isinstance(resp.json, dict) else {}
    return _upsert_ailos_boleto(db, billing.id, payload, response_body)


def gerar_boleto_lote(db: Session, billings: list[Billing], clients_by_id: dict[int, Client]) -> AilosLote:
    """Gera um lote assíncrono de boletos. Retorna o ``AilosLote`` (status='processing')."""
    payloads = [montar_payload_boleto(b, clients_by_id[b.client_id]) for b in billings]

    body_lote = {
        'convenioCobranca': {'codigoCarteiraCobranca': settings.ailos_default_carteira},
        'boletos': payloads,
    }

    resp = ailos_client.request(
        db, 'POST',
        _PATH_GERAR_BOLETO_LOTE.format(convenio=settings.ailos_numero_convenio),
        json_body=body_lote,
    )

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
    db.commit()
    db.refresh(lote)

    for b, payload in zip(billings, payloads):
        _upsert_ailos_boleto(db, b.id, payload, None, lote_id=lote.id)

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
    payloads = []
    billings = []
    for numero_parcela, b in billings_by_parcela:
        payload = montar_payload_boleto(b, clients_by_id[b.client_id])
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

    body_lote = {
        'convenioCobranca': {'codigoCarteiraCobranca': settings.ailos_default_carteira},
        'carnes': payloads,
    }

    resp = ailos_client.request(
        db, 'POST',
        _PATH_GERAR_CARNE_LOTE.format(convenio=settings.ailos_numero_convenio),
        json_body=body_lote,
    )

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
    db.commit()
    db.refresh(lote)

    for b, payload in zip(billings, payloads):
        _upsert_ailos_boleto(db, b.id, payload, None, lote_id=lote.id)

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


def consultar_lote(db: Session, lote: AilosLote) -> dict:
    """
    Consulta o status de um lote/carnê pelo ``ticket``.

    Enquanto a Ailos ainda estiver processando, retorna
    ``{'status': 'processing'}`` sem alterar ``lote.status``. Quando
    concluído, atualiza ``lote.status='completed'``, ``payload_response`` e
    faz upsert em ``ailos_boletos`` para cada item retornado.
    """
    path = _PATH_CONSULTAR_CARNE_LOTE if lote.tipo == 'carne' else _PATH_CONSULTAR_LOTE
    resp = ailos_client.request(
        db, 'GET',
        path.format(convenio=settings.ailos_numero_convenio, ticket=lote.ticket),
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
    )
