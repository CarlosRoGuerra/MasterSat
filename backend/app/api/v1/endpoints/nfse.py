"""
Endpoints da NFS-e de Joinville (Pública / Nota Nacional).

POST /nfse/emitir/{billing_id}    → emite a NFS-e da cobrança (ADMIN/FINANCEIRO)
GET  /nfse/{billing_id}           → status/dados da NFS-e da cobrança
POST /nfse/consultar/{billing_id} → reconsulta o protocolo (quando 'processing')
"""
from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import UserRole
from app.models.nfse_nota import NfseNota
from app.schemas.nfse import NfseOut
from app.services import nfse_joinville
from app.services.nfse_joinville import NfseApiError, NfseError

router = APIRouter()

ALLOWED_ROLES = (UserRole.ADMIN, UserRole.FINANCIAL)


def _raise_nfse_error(exc: Exception) -> NoReturn:
    if isinstance(exc, NfseApiError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if isinstance(exc, NfseError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


@router.post('/emitir/{billing_id}', response_model=NfseOut)
def emitir(
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    billing = db.get(Billing, billing_id)
    if billing is None or getattr(billing, 'is_deleted', False):
        raise HTTPException(status_code=404, detail='Cobrança não encontrada')
    client = db.get(Client, billing.client_id)
    if client is None:
        raise HTTPException(status_code=404, detail='Cliente da cobrança não encontrado')
    try:
        return nfse_joinville.emitir_nfse(db, billing, client)
    except (NfseError, NfseApiError) as exc:
        _raise_nfse_error(exc)


@router.get('/{billing_id}', response_model=NfseOut)
def obter(
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    nota = db.query(NfseNota).filter_by(billing_id=billing_id).first()
    if nota is None:
        raise HTTPException(status_code=404, detail='NFS-e não encontrada para esta cobrança')
    return nota


@router.post('/consultar/{billing_id}', response_model=NfseOut)
def consultar(
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    nota = db.query(NfseNota).filter_by(billing_id=billing_id).first()
    if nota is None:
        raise HTTPException(status_code=404, detail='NFS-e não encontrada para esta cobrança')
    if not nota.protocolo:
        raise HTTPException(status_code=400, detail='NFS-e ainda não enviada (sem protocolo)')
    try:
        return nfse_joinville.consultar(db, nota)
    except (NfseError, NfseApiError) as exc:
        _raise_nfse_error(exc)
