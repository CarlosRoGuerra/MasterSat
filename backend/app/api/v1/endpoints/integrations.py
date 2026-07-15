"""
Endpoints de integração máquina-a-máquina (ex.: CobraZap puxa os boletos para
disparar a cobrança via WhatsApp).

Autenticação: header ``X-API-Key`` (ver INTEGRATION_API_KEY no .env). NÃO usa o
login JWT do painel — é uma chave de serviço dedicada ao parceiro.

GET /integrations/cobrancas                  → lista cobranças em aberto (JSON)
GET /integrations/cobrancas/{billing_id}     → detalhe de uma cobrança (JSON)
GET /integrations/cobrancas/{billing_id}/pdf → PDF do boleto
"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import require_api_key
from app.api.v1.endpoints.boletos import public_boleto_url
from app.core.config import settings
from app.db.session import get_db
from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus
from app.services.ailos_boletos import aplicar_dados_oficiais_ailos
from app.services.boleto_ailos import gerar_dados_boleto
from app.services.boleto_pdf import gerar_boleto_pdf
from app.services.financial import refresh_overdue_statuses, valor_com_juros

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
        # 'email' | 'whatsapp' | 'todos' — define como o cliente quer receber.
        'forma_envio': client.delivery_method or 'email',
        # Atalho do cadastro: "Enviar boleto via Whats" marcado no cliente.
        'enviar_boleto_whatsapp': bool(client.send_boleto_whatsapp),
        'nosso_numero': None,
        'linha_digitavel': None,
        'codigo_barras': None,
        'pix_copia_cola': None,
        'boleto_registrado': bool(ailos_boleto and ailos_boleto.linha_digitavel),
        'boleto_pdf_url': f'{base}{settings.api_v1_prefix}/integrations/cobrancas/{billing.id}/pdf',
        # Link SEM autenticação (token HMAC) — pode ir direto na mensagem ao cliente
        'boleto_link_cliente': public_boleto_url(billing.id),
    }
    # Valor atualizado com multa/juros para cobranças vencidas (fonte: backend)
    payload['valor_com_juros'] = (
        valor_com_juros(billing.amount, billing.due_date)
        if getattr(billing.status, 'value', billing.status) == 'vencida' else None
    )

    # SÓ entrega os dados de pagamento de boleto REGISTRADO na Ailos: título sem
    # registro é recusado pelo banco na hora de pagar — enviar linha digitável
    # local geraria cobrança impagável na mão do cliente final.
    if not payload['boleto_registrado']:
        payload['boleto_link_cliente'] = None
        return payload

    # Geração do boleto não pode derrubar a lista inteira: se uma cobrança falhar
    # (ex.: dado inconsistente), devolvemos a cobrança com os campos de boleto nulos.
    try:
        dados = _dados_boleto(billing, client, ailos_boleto)
        payload.update({
            'nosso_numero': dados.nosso_numero_display,
            'linha_digitavel': dados.linha_digitavel,
            'codigo_barras': dados.codigo_barras,
            'pix_copia_cola': dados.pix_emv,
        })
    except Exception:  # noqa: BLE001 — degrada graciosamente, sem quebrar o lote
        logger.warning('Falha ao gerar dados do boleto para a cobrança %s', billing.id, exc_info=True)
    return payload


@router.get('/cobrancas')
def listar_cobrancas(
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key),
    forma_envio: str | None = Query(
        default=None,
        description="Filtra pela forma de envio do cliente: 'whatsapp' ou 'email' (inclui quem usa 'todos').",
    ),
    limit: int = Query(default=500, ge=1, le=2000),
):
    """Lista as cobranças em aberto (pendentes/vencidas) com os dados de boleto/Pix
    prontos para envio. Usado pelo CobraZap para puxar os boletos."""
    refresh_overdue_statuses(db)

    query = (
        db.query(Billing, Client, AilosBoleto)
        .join(Client, Client.id == Billing.client_id)
        .outerjoin(AilosBoleto, AilosBoleto.billing_id == Billing.id)
        .filter(
            Billing.is_deleted.is_(False),
            Client.is_deleted.is_(False),
            Billing.status.in_(_OPEN_STATUSES),
        )
    )
    if forma_envio == 'whatsapp':
        # Tipo de Envio 'whatsapp'/'todos' OU o atalho "Enviar boleto via Whats" marcado
        query = query.filter(
            Client.delivery_method.in_(['whatsapp', 'todos'])
            | Client.send_boleto_whatsapp.is_(True)
        )
    elif forma_envio == 'email':
        query = query.filter(Client.delivery_method.in_(['email', 'todos']))

    rows = query.order_by(Billing.due_date.asc()).limit(limit).all()
    cobrancas = [_cobranca_payload(billing, client, boleto) for billing, client, boleto in rows]
    return {'total': len(cobrancas), 'cobrancas': cobrancas}


def _get_cobranca_or_404(billing_id: int, db: Session) -> tuple[Billing, Client, AilosBoleto | None]:
    billing = db.get(Billing, billing_id)
    if not billing or billing.is_deleted:
        raise HTTPException(status_code=404, detail='Cobrança não encontrada')
    client = db.get(Client, billing.client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail='Cliente não encontrado')
    ailos_boleto = db.query(AilosBoleto).filter_by(billing_id=billing.id).first()
    return billing, client, ailos_boleto


@router.get('/cobrancas/{billing_id}')
def detalhar_cobranca(
    billing_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key),
):
    """Detalhe de uma cobrança específica (mesmos campos da listagem)."""
    billing, client, ailos_boleto = _get_cobranca_or_404(billing_id, db)
    return _cobranca_payload(billing, client, ailos_boleto)


@router.get('/cobrancas/{billing_id}/pdf')
def baixar_boleto_pdf(
    billing_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key),
):
    """PDF do boleto para anexar no WhatsApp/e-mail."""
    billing, client, ailos_boleto = _get_cobranca_or_404(billing_id, db)
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
