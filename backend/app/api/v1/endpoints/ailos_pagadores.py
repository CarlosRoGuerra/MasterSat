"""
Endpoints de cadastro/consulta de pagadores via API de Cobrança Ailos.

POST /ailos/pagadores                    → Cadastra cliente como pagador
PUT  /ailos/pagadores                    → Altera dados do pagador
GET  /ailos/pagadores/{numero_inscricao} → Consulta pagador por CPF/CNPJ
GET  /ailos/pagadores                    → Lista pagadores cadastrados
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.v1.endpoints.ailos_common import ALLOWED_ROLES, raise_ailos_error
from app.db.session import get_db
from app.models.client import Client
from app.schemas.ailos import AilosPagadorIn, AilosPagadorOut
from app.services.ailos_client import AilosApiError, AilosError
from app.services.ailos_pagadores import (
    alterar_pagador,
    cadastrar_pagador,
    consultar_pagador,
    listar_pagadores,
)
from app.services.ailos_validators import AilosValidationError

router = APIRouter()

_AILOS_EXCEPTIONS = (AilosValidationError, AilosError, AilosApiError)


def _get_client_or_404(client_id: int, db: Session) -> Client:
    c = db.get(Client, client_id)
    if not c or c.is_deleted:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    return c


@router.post('/pagadores', response_model=AilosPagadorOut)
def cadastrar_pagador_endpoint(
    payload: AilosPagadorIn,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Cadastra o cliente informado como pagador na Ailos."""
    client = _get_client_or_404(payload.client_id, db)
    try:
        return AilosPagadorOut(data=cadastrar_pagador(db, client))
    except _AILOS_EXCEPTIONS as exc:
        raise_ailos_error(exc)


@router.put('/pagadores', response_model=AilosPagadorOut)
def alterar_pagador_endpoint(
    payload: AilosPagadorIn,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Atualiza os dados do cliente informado como pagador na Ailos."""
    client = _get_client_or_404(payload.client_id, db)
    try:
        return AilosPagadorOut(data=alterar_pagador(db, client))
    except _AILOS_EXCEPTIONS as exc:
        raise_ailos_error(exc)


@router.get('/pagadores/{numero_inscricao}', response_model=AilosPagadorOut)
def consultar_pagador_endpoint(
    numero_inscricao: str,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Consulta um pagador cadastrado na Ailos pelo CPF/CNPJ."""
    try:
        return AilosPagadorOut(data=consultar_pagador(db, numero_inscricao))
    except _AILOS_EXCEPTIONS as exc:
        raise_ailos_error(exc)


@router.get('/pagadores', response_model=AilosPagadorOut)
def listar_pagadores_endpoint(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Lista os pagadores cadastrados na Ailos."""
    try:
        return AilosPagadorOut(data=listar_pagadores(db))
    except _AILOS_EXCEPTIONS as exc:
        raise_ailos_error(exc)
