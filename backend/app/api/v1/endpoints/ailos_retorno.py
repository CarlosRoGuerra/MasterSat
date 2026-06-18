"""
Endpoints de solicitação/listagem/download de arquivos de retorno via API de
Cobrança Ailos.

POST /ailos/retorno/solicitar         → Solicita arquivo de retorno (data de movimento)
GET  /ailos/retorno/listar            → Lista arquivos de retorno disponíveis
GET  /ailos/retorno/baixar/{ticket}   → Baixa (e armazena no MinIO) um arquivo de retorno
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.v1.endpoints.ailos_common import ALLOWED_ROLES, raise_ailos_error
from app.db.session import get_db
from app.models.ailos_retorno_arquivo import AilosRetornoArquivo
from app.schemas.ailos import AilosRetornoArquivoOut, AilosRetornoListarOut, AilosRetornoSolicitarIn
from app.services.ailos_client import AilosApiError, AilosError
from app.services.ailos_retorno import baixar_arquivo_retorno, listar_arquivos_retorno, solicitar_arquivo_retorno
from app.services.ailos_validators import AilosValidationError

router = APIRouter()

_AILOS_EXCEPTIONS = (AilosValidationError, AilosError, AilosApiError)


@router.post('/retorno/solicitar', response_model=AilosRetornoArquivoOut)
def solicitar_retorno_endpoint(
    payload: AilosRetornoSolicitarIn,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Solicita à Ailos a geração do arquivo de retorno de uma data de movimento."""
    try:
        return solicitar_arquivo_retorno(db, payload.data_movimento)
    except _AILOS_EXCEPTIONS as exc:
        raise_ailos_error(exc)


@router.get('/retorno/listar', response_model=AilosRetornoListarOut)
def listar_retorno_endpoint(
    data_inicial: date = Query(...),
    data_final: date = Query(...),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Lista os arquivos de retorno disponíveis na Ailos num intervalo de datas."""
    try:
        return AilosRetornoListarOut(data=listar_arquivos_retorno(db, data_inicial, data_final))
    except _AILOS_EXCEPTIONS as exc:
        raise_ailos_error(exc)


@router.get('/retorno/baixar/{ticket}', response_model=AilosRetornoArquivoOut)
def baixar_retorno_endpoint(
    ticket: str,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Baixa o arquivo de retorno (ZIP) de um ticket já solicitado e o armazena no MinIO."""
    row = db.query(AilosRetornoArquivo).filter_by(ticket=ticket).first()
    if row is None:
        raise HTTPException(status_code=404, detail='Arquivo de retorno não encontrado')
    try:
        return baixar_arquivo_retorno(db, row)
    except _AILOS_EXCEPTIONS as exc:
        raise_ailos_error(exc)
