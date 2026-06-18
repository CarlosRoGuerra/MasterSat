"""Helpers compartilhados pelos endpoints da integração Ailos (Cobrança API)."""
from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from app.models.enums import UserRole
from app.services.ailos_client import AilosApiError, AilosError
from app.services.ailos_validators import AilosValidationError

ALLOWED_ROLES = (UserRole.ADMIN, UserRole.FINANCIAL)


def raise_ailos_error(exc: Exception) -> NoReturn:
    """Converte exceções da camada de integração Ailos em HTTPException."""
    if isinstance(exc, AilosValidationError):
        raise HTTPException(status_code=422, detail={'errors': exc.errors}) from exc
    if isinstance(exc, AilosApiError):
        raise HTTPException(
            status_code=502,
            detail={
                'friendly_message': exc.friendly_message,
                'ailos_message': exc.ailos_message,
                'ailos_code': exc.ailos_code,
                'status_code': exc.status_code,
            },
        ) from exc
    if isinstance(exc, AilosError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc
