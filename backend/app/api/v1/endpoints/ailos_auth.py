"""
Endpoints de autenticação/autorização da integração Ailos (Cobrança API).

GET  /ailos/status     → Status da integração (não expõe tokens)
POST /ailos/connect    → Inicia autorização do cooperado (ADMIN) — retorna login_url
POST /ailos/callback   → Callback OAuth2 do cooperado (público, chamado pela Ailos)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.v1.endpoints.ailos_common import ALLOWED_ROLES, raise_ailos_error
from app.core.config import settings
from app.db.session import get_db
from app.models.ailos_integration import AilosIntegration
from app.models.enums import UserRole
from app.schemas.ailos import AilosCallbackIn, AilosConnectOut, AilosStatusOut
from app.services.ailos_client import (
    AilosApiError,
    AilosError,
    build_cooperado_login_url,
    handle_cooperado_callback,
)

router = APIRouter()


@router.get('/status', response_model=AilosStatusOut)
def get_status(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Retorna o status atual da integração (credenciais configuradas, autorização do cooperado)."""
    integration = db.query(AilosIntegration).order_by(AilosIntegration.id.asc()).first()

    client_token_configured = bool(
        settings.ailos_client_id and settings.ailos_client_secret and settings.ailos_apim_base_url
    )

    return AilosStatusOut(
        client_token_configured=client_token_configured,
        cooperado_status=integration.status if integration else 'pending',
        numero_convenio=integration.numero_convenio if integration else settings.ailos_numero_convenio,
        authorized_at=integration.authorized_at if integration else None,
        last_refresh_at=integration.last_refresh_at if integration else None,
        last_error=integration.last_error if integration else None,
    )


@router.post('/connect', response_model=AilosConnectOut)
def connect(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN)),
):
    """
    Inicia o fluxo de autorização do cooperado junto à Ailos.

    Retorna a URL de login para um administrador abrir manualmente no
    navegador. Após autorizar, a Ailos redireciona para
    ``AILOS_CALLBACK_URL`` com ``state``/``code``, que devem ser enviados a
    ``POST /ailos/callback``.
    """
    try:
        login_url, state = build_cooperado_login_url(db)
    except (AilosError, AilosApiError) as exc:
        raise_ailos_error(exc)
    return AilosConnectOut(login_url=login_url, state=state)


@router.post('/callback')
def callback(
    payload: AilosCallbackIn,
    db: Session = Depends(get_db),
):
    """Callback OAuth2 do cooperado — chamado pela Ailos, sem autenticação JWT."""
    try:
        handle_cooperado_callback(db, payload.state, payload.code)
    except AilosError as exc:
        raise_ailos_error(exc)
    return {}
