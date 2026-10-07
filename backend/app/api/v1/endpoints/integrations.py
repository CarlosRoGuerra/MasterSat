"""
Endpoints de integração máquina-a-máquina (ex.: CobraZap puxa os boletos para
disparar a cobrança via WhatsApp).

Autenticação: header ``X-API-Key`` (ver INTEGRATION_API_KEY no .env). NÃO usa o
login JWT do painel — é uma chave de serviço dedicada ao parceiro.

GET /integrations/cobrancas                  → consulta paginada por status/datas (JSON)
GET /integrations/cobrancas/{billing_id}     → detalhe de uma cobrança (JSON)
GET /integrations/cobrancas/{billing_id}/pdf → PDF do boleto
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import case
from sqlalchemy.orm import Session, aliased

from app.api.deps import require_api_key
from app.api.v1.endpoints.boletos import public_boleto_url
from app.core.config import settings
from app.db.session import get_db
from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.schemas.integration_billing import IntegrationBillingOut, IntegrationBillingPage
from app.services.ailos_boletos import aplicar_dados_oficiais_ailos, resolver_pagador
from app.services.boleto_ailos import gerar_dados_boleto
from app.services.boleto_pdf import gerar_boleto_pdf
from app.services.financial import valor_com_juros

router = APIRouter()
logger = logging.getLogger(__name__)

_OPEN_STATUSES = (BillingStatus.PENDING, BillingStatus.OVERDUE)


def _endereco(client: Client) -> str:
    return ' '.join(filter(None, [client.address_line, client.address_number, client.address_complement]))


def _dados_boleto(billing: Billing, client: Client, ailos_boleto: AilosBoleto | None):
    """Monta os dados do boleto (linha digitável, código de barras, Pix) reusando
    a mesma lógica do endpoint interno de boletos."""
    dados = gerar_dados_boleto(
        billing_id=billing.id,
        valor=billing.amount,
        vencimento=billing.due_date,
        sacado_nome=client.name,
        sacado_cpf_cnpj=client.cpf_cnpj or '',
        sacado_endereco=_endereco(client),
        data_emissao=billing.created_at.date() if billing.created_at else date.today(),
        sacado_cidade=client.city or '',
        sacado_cep=client.zip_code or '',
        sacado_uf=client.state or '',
        sacado_ie=client.rg_ie or '',
        itens=[(billing.title or 'SERVIÇO DE RASTREAMENTO', float(billing.amount))],
    )
    return aplicar_dados_oficiais_ailos(dados, ailos_boleto)


def _cobranca_payload(billing: Billing, client: Client, ailos_boleto: AilosBoleto | None) -> dict:
    base = (settings.backend_public_url or '').rstrip('/')
    registrado = bool(ailos_boleto and ailos_boleto.linha_digitavel and ailos_boleto.codigo_barras)
    motivo = _motivo_boleto_indisponivel(billing, ailos_boleto)
    payload = {
        'id': billing.id,
        'titulo': billing.title,
        'cliente': {
            'id': client.id,
            'nome': client.name,
            'cpf_cnpj': client.cpf_cnpj,
            'telefone': client.phone,
            'email': client.email,
        },
        'valor': float(billing.amount),
        'vencimento': billing.due_date.isoformat() if billing.due_date else None,
        'status': getattr(billing.status, 'value', str(billing.status)),
        # O estado financeiro registrado é a fonte de confirmação; ausência
        # na lista de cobranças abertas nunca deve ser interpretada como baixa.
        'pagamento_confirmado': billing.status == BillingStatus.PAID,
        'data_pagamento': billing.payment_date,
        'valor_pago': float(billing.paid_amount) if billing.paid_amount is not None else None,
        'forma_pagamento': billing.payment_method,
        # 'email' | 'whatsapp' | 'todos' — define como o cliente quer receber.
        'forma_envio': client.delivery_method or 'email',
        # Atalho do cadastro: "Enviar boleto via Whats" marcado no cliente.
        'enviar_boleto_whatsapp': bool(client.send_boleto_whatsapp),
        'nosso_numero': None,
        'linha_digitavel': None,
        'codigo_barras': None,
        'pix_copia_cola': None,
        'boleto_registrado': registrado,
        'boleto_disponivel': motivo is None,
        'motivo_boleto_indisponivel': motivo,
        'boleto_pdf_url': (
            f'{base}{settings.api_v1_prefix}/integrations/cobrancas/{billing.id}/pdf'
            if motivo is None else None
        ),
        # Link SEM autenticação (token HMAC) — pode ir direto na mensagem ao cliente
        'boleto_link_cliente': public_boleto_url(billing.id) if motivo is None else None,
    }
    # Valor atualizado com multa/juros para cobranças vencidas (fonte: backend)
    payload['valor_com_juros'] = (
        valor_com_juros(billing.amount, billing.due_date)
        if getattr(billing.status, 'value', billing.status) == 'vencida' else None
    )

    # SÓ entrega os dados de pagamento de boleto REGISTRADO na Ailos: título sem
    # registro é recusado pelo banco na hora de pagar — enviar linha digitável
    # local geraria cobrança impagável na mão do cliente final.
    if motivo is not None:
        return payload

    # A consulta entrega os dados oficiais armazenados, sem calcular códigos
    # locais ou depender da geração de PDF para serializar um título antigo.
    payload.update({
        'nosso_numero': ailos_boleto.nosso_numero,
        'linha_digitavel': ailos_boleto.linha_digitavel,
        'codigo_barras': ailos_boleto.codigo_barras,
        'pix_copia_cola': ailos_boleto.pix_emv,
    })
    return payload


def _motivo_boleto_indisponivel(billing: Billing, boleto: AilosBoleto | None) -> str | None:
    if billing.status not in _OPEN_STATUSES:
        return 'cobranca_paga' if billing.status == BillingStatus.PAID else 'cobranca_cancelada'
    if billing.somente_sistema:
        return 'somente_sistema'
    if boleto is None or not (boleto.linha_digitavel and boleto.codigo_barras):
        return 'sem_registro_bancario'
    if boleto.baixa_status is not None or boleto.status_ailos in ('3', '5'):
        return 'boleto_baixado'
    return None


@router.get('/cobrancas', response_model=IntegrationBillingPage)
def listar_cobrancas(
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key),
    forma_envio: Literal['whatsapp', 'email'] | None = Query(
        default=None,
        description="Filtra pela forma de envio do pagador: 'whatsapp' ou 'email' (inclui quem usa 'todos').",
    ),
    status: BillingStatus | Literal['todos'] | None = Query(
        default=None,
        description="Situação: pendente, vencida, paga, cancelada ou todos. Sem filtro, retorna pendentes/vencidas.",
    ),
    vencimento: date | None = Query(default=None, description='Vencimento exato (AAAA-MM-DD).'),
    vencimento_de: date | None = Query(default=None, description='Vencimento inicial, inclusive (AAAA-MM-DD).'),
    vencimento_ate: date | None = Query(default=None, description='Vencimento final, inclusive (AAAA-MM-DD).'),
    pagamento_de: date | None = Query(default=None, description='Data de pagamento inicial, inclusive (AAAA-MM-DD).'),
    pagamento_ate: date | None = Query(default=None, description='Data de pagamento final, inclusive (AAAA-MM-DD).'),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0, description='Quantidade de registros a pular na consulta filtrada.'),
):
    """Consulta paginada de cobranças e pagamentos registrados no MasterSat.

    Sem status explícito, mantém a lista de cobranças abertas. Todos os
    filtros se combinam; as datas inicial e final são inclusivas.
    """
    for inicio, fim, campo in (
        (vencimento_de, vencimento_ate, 'vencimento'),
        (pagamento_de, pagamento_ate, 'pagamento'),
    ):
        if inicio is not None and fim is not None and inicio > fim:
            raise HTTPException(status_code=422, detail=f'Intervalo de {campo} inválido: início posterior ao fim.')
    # Reclassificação pendente<->vencida roda no worker horário (BE-05) — não
    # precisa ser refeita a cada chamada do webhook.
    # O filtro de canal precisa usar o MESMO pagador que aparece no retorno.
    # Snapshot explícito > interveniente ativo do contrato legado > origem.
    Interveniente = aliased(Client)
    Pagador = aliased(Client)
    pagador_id = case(
        (Billing.payer_client_id.is_not(None), Billing.payer_client_id),
        (Interveniente.is_deleted.is_(False), Contract.interveniente_client_id),
        else_=Billing.client_id,
    )
    query = (
        db.query(Billing, Pagador, AilosBoleto)
        .join(Client, Client.id == Billing.client_id)
        .outerjoin(Contract, Contract.id == Billing.contract_id)
        .outerjoin(Interveniente, Interveniente.id == Contract.interveniente_client_id)
        .join(Pagador, Pagador.id == pagador_id)
        .outerjoin(AilosBoleto, AilosBoleto.billing_id == Billing.id)
        .filter(
            Billing.is_deleted.is_(False),
            Client.is_deleted.is_(False),
            Pagador.is_deleted.is_(False),
        )
    )
    if status is None:
        query = query.filter(Billing.status.in_(_OPEN_STATUSES))
    elif status != 'todos':
        query = query.filter(Billing.status == status)
    if vencimento is not None:
        query = query.filter(Billing.due_date == vencimento)
    if vencimento_de is not None:
        query = query.filter(Billing.due_date >= vencimento_de)
    if vencimento_ate is not None:
        query = query.filter(Billing.due_date <= vencimento_ate)
    if pagamento_de is not None:
        query = query.filter(Billing.payment_date >= pagamento_de)
    if pagamento_ate is not None:
        query = query.filter(Billing.payment_date <= pagamento_ate)
    if forma_envio == 'whatsapp':
        # Tipo de Envio 'whatsapp'/'todos' OU o atalho "Enviar boleto via Whats" marcado
        query = query.filter(
            Pagador.delivery_method.in_(['whatsapp', 'todos'])
            | Pagador.send_boleto_whatsapp.is_(True)
        )
    elif forma_envio == 'email':
        query = query.filter(
            Pagador.delivery_method.in_(['email', 'todos']) | Pagador.delivery_method.is_(None)
            | (Pagador.delivery_method == '')
        )

    total_registros = query.count()
    rows = query.order_by(Billing.due_date.asc(), Billing.id.asc()).offset(offset).limit(limit).all()
    cobrancas = [_cobranca_payload(billing, client, boleto) for billing, client, boleto in rows]
    has_more = offset + len(cobrancas) < total_registros
    return {
        'total': len(cobrancas), 'total_registros': total_registros,
        'limit': limit, 'offset': offset, 'has_more': has_more,
        'next_offset': offset + len(cobrancas) if has_more else None,
        'cobrancas': cobrancas,
    }


def _get_cobranca_or_404(billing_id: int, db: Session) -> tuple[Billing, Client, AilosBoleto | None]:
    billing = db.get(Billing, billing_id)
    if not billing or billing.is_deleted:
        raise HTTPException(status_code=404, detail='Cobrança não encontrada')
    client = db.get(Client, billing.client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail='Cliente não encontrado')
    # Pagador da cobrança = interveniente do contrato, quando houver.
    try:
        client = resolver_pagador(db, billing, client)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail='Responsável financeiro não disponível') from exc
    ailos_boleto = db.query(AilosBoleto).filter_by(billing_id=billing.id).first()
    return billing, client, ailos_boleto


@router.get('/cobrancas/{billing_id}', response_model=IntegrationBillingOut)
def detalhar_cobranca(
    billing_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key),
):
    """Detalhe de uma cobrança específica (mesmos campos da listagem)."""
    billing, client, ailos_boleto = _get_cobranca_or_404(billing_id, db)
    return _cobranca_payload(billing, client, ailos_boleto)


@router.get(
    '/cobrancas/{billing_id}/pdf',
    response_class=Response,
    responses={
        200: {
            'description': 'PDF do boleto registrado disponível para pagamento.',
            'content': {'application/pdf': {'schema': {'type': 'string', 'format': 'binary'}}},
        },
        409: {
            'description': 'Boleto indisponível; detail.code informa o motivo.',
            'content': {'application/json': {'schema': {}}},
        },
        422: {
            'description': 'ID inválido ou falha ao montar o documento.',
            'content': {'application/json': {'schema': {}}},
        },
    },
)
def baixar_boleto_pdf(
    billing_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key),
):
    """PDF do boleto para anexar no WhatsApp/e-mail."""
    billing, client, ailos_boleto = _get_cobranca_or_404(billing_id, db)
    motivo = _motivo_boleto_indisponivel(billing, ailos_boleto)
    if motivo is not None:
        raise HTTPException(
            status_code=409,
            detail={'code': motivo, 'message': 'Esta cobrança não tem boleto disponível para pagamento.'},
        )
    try:
        dados = _dados_boleto(billing, client, ailos_boleto)
        pdf_bytes = gerar_boleto_pdf(dados)
    except Exception:  # noqa: BLE001
        logger.warning('Falha ao gerar PDF do boleto da cobrança %s', billing_id, exc_info=True)
        raise HTTPException(status_code=422, detail='Não foi possível gerar o boleto desta cobrança.')
    return Response(
        content=pdf_bytes,
        media_type='application/pdf',
        headers={'Content-Disposition': f'inline; filename="boleto_{billing.id:06d}.pdf"'},
    )
