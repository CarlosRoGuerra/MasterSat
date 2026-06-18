"""
Endpoints de geração e consulta de boletos via API de Cobrança Ailos.

POST /ailos/boletos              → Gera boleto único (síncrono)
POST /ailos/boletos/lote          → Gera lote de boletos (assíncrono)
POST /ailos/carne/lote             → Gera carnê (lote de parcelas, assíncrono)
GET  /ailos/boletos/{numero_boleto}→ Consulta boleto na Ailos
GET  /ailos/lotes/{ticket}         → Consulta status de um lote/carnê
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.v1.endpoints.ailos_common import ALLOWED_ROLES, raise_ailos_error
from app.db.session import get_db
from app.models.ailos_boleto import AilosBoleto
from app.models.ailos_lote import AilosLote
from app.models.billing import Billing
from app.models.client import Client
from app.schemas.ailos import (
    AilosBoletoOut,
    AilosGerarBoletoIn,
    AilosGerarLoteIn,
    AilosLoteOut,
    AilosLoteStatusOut,
)
from app.services.ailos_boletos import (
    consultar_boleto,
    consultar_lote,
    gerar_boleto,
    gerar_boleto_lote,
    gerar_carne_lote,
)
from app.services.ailos_client import AilosApiError, AilosError
from app.services.ailos_validators import AilosValidationError

router = APIRouter()

_AILOS_EXCEPTIONS = (AilosValidationError, AilosError, AilosApiError)


def _get_billing_or_404(billing_id: int, db: Session) -> Billing:
    b = db.get(Billing, billing_id)
    if not b or b.is_deleted:
        raise HTTPException(status_code=404, detail="Cobrança não encontrada")
    return b


def _get_client_or_404(client_id: int, db: Session) -> Client:
    c = db.get(Client, client_id)
    if not c or c.is_deleted:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    return c


def _resolve_billings_and_clients(billing_ids: list[int], db: Session) -> tuple[list[Billing], dict[int, Client]]:
    billings: list[Billing] = []
    clients_by_id: dict[int, Client] = {}
    for billing_id in billing_ids:
        billing = _get_billing_or_404(billing_id, db)
        client = _get_client_or_404(billing.client_id, db)
        billings.append(billing)
        clients_by_id[client.id] = client
    return billings, clients_by_id


# ---------------------------------------------------------------------------
# POST /ailos/boletos — boleto único (síncrono)
# ---------------------------------------------------------------------------

@router.post('/boletos', response_model=AilosBoletoOut)
def gerar_boleto_endpoint(
    payload: AilosGerarBoletoIn,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Gera (ou regenera) um boleto via API Ailos para o billing informado."""
    billing = _get_billing_or_404(payload.billing_id, db)
    client = _get_client_or_404(billing.client_id, db)

    try:
        return gerar_boleto(db, billing, client)
    except _AILOS_EXCEPTIONS as exc:
        raise_ailos_error(exc)


# ---------------------------------------------------------------------------
# POST /ailos/boletos/lote — lote de boletos (assíncrono)
# ---------------------------------------------------------------------------

@router.post('/boletos/lote', response_model=AilosLoteOut)
def gerar_boleto_lote_endpoint(
    payload: AilosGerarLoteIn,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Submete um lote de boletos à Ailos. Use GET /ailos/lotes/{ticket} para acompanhar."""
    billings, clients_by_id = _resolve_billings_and_clients(payload.billing_ids, db)

    try:
        return gerar_boleto_lote(db, billings, clients_by_id)
    except _AILOS_EXCEPTIONS as exc:
        raise_ailos_error(exc)


# ---------------------------------------------------------------------------
# POST /ailos/carne/lote — carnê (lote de parcelas, assíncrono)
# ---------------------------------------------------------------------------

@router.post('/carne/lote', response_model=AilosLoteOut)
def gerar_carne_lote_endpoint(
    payload: AilosGerarLoteIn,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """
    Submete um carnê à Ailos. As parcelas são numeradas (1, 2, 3, ...) na
    ordem de ``billing_ids``. Use GET /ailos/lotes/{ticket} para acompanhar.
    """
    billings, clients_by_id = _resolve_billings_and_clients(payload.billing_ids, db)
    billings_by_parcela = list(enumerate(billings, start=1))

    try:
        return gerar_carne_lote(db, billings_by_parcela, clients_by_id)
    except _AILOS_EXCEPTIONS as exc:
        raise_ailos_error(exc)


# ---------------------------------------------------------------------------
# GET /ailos/boletos/{numero_boleto} — consulta na Ailos
# ---------------------------------------------------------------------------

@router.get('/boletos/{numero_boleto}')
def consultar_boleto_endpoint(
    numero_boleto: str,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Consulta um boleto diretamente na API Ailos pelo identificador retornado na geração."""
    try:
        return consultar_boleto(db, numero_boleto)
    except _AILOS_EXCEPTIONS as exc:
        raise_ailos_error(exc)


# ---------------------------------------------------------------------------
# GET /ailos/lotes/{ticket} — status de lote/carnê
# ---------------------------------------------------------------------------

@router.get('/lotes/{ticket}', response_model=AilosLoteStatusOut)
def get_lote_status(
    ticket: str,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Consulta o status de um lote/carnê. Se concluído, atualiza os boletos correspondentes."""
    lote = db.query(AilosLote).filter_by(ticket=ticket).first()
    if lote is None:
        raise HTTPException(status_code=404, detail="Lote não encontrado")

    if lote.status == 'processing':
        try:
            result = consultar_lote(db, lote)
        except _AILOS_EXCEPTIONS as exc:
            raise_ailos_error(exc)

        if result.get('status') == 'processing':
            return AilosLoteStatusOut(ticket=lote.ticket, status='processing', boletos=None)
        lote = result['lote']

    boletos = db.query(AilosBoleto).filter_by(lote_id=lote.id).all()
    return AilosLoteStatusOut(
        ticket=lote.ticket,
        status=lote.status,
        boletos=[AilosBoletoOut.model_validate(b) for b in boletos],
    )
