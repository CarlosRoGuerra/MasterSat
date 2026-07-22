"""
Endpoints da NFS-e.

O provedor é escolhido por ``NFSE_PROVEDOR``:
  - ``nacional``  → Emissor Nacional (Sefin Nacional). Padrão desde 20/07/2026,
    quando Joinville encerrou a emissão municipal.
  - ``joinville`` → webservice municipal legado. Só serve para consultar notas
    antigas; emitir por ele hoje retorna E930.

GET  /nfse/                       → lista notas (filtro por client_id) p/ o painel
POST /nfse/emitir/{billing_id}    → emite a NFS-e da cobrança (ADMIN/FINANCEIRO)
GET  /nfse/{billing_id}           → status/dados da NFS-e da cobrança
POST /nfse/consultar/{billing_id} → reconsulta a nota junto ao provedor
"""
from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.config import settings
from app.db.session import get_db
from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import UserRole
from app.models.nfse_nota import NfseNota
from app.schemas.nfse import NfseClientItem, NfseOut
from app.services import nfse_joinville, nfse_nacional

router = APIRouter()

ALLOWED_ROLES = (UserRole.ADMIN, UserRole.FINANCIAL)

# Os dois módulos definem NfseError/NfseApiError próprios; o handler trata ambos.
_ERROS_CONFIG = (nfse_joinville.NfseError, nfse_nacional.NfseError)
_ERROS_API = (nfse_joinville.NfseApiError, nfse_nacional.NfseApiError)


def _provedor():
    return nfse_joinville if settings.nfse_provedor == 'joinville' else nfse_nacional


def _raise_nfse_error(exc: Exception) -> NoReturn:
    if isinstance(exc, _ERROS_API):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if isinstance(exc, _ERROS_CONFIG):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


@router.get('/', response_model=list[NfseClientItem])
def listar(
    client_id: int | None = None,
    limit: int = Query(default=100, le=300),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Lista as NFS-e emitidas/em processamento, com filtro por cliente
    (usado no botão de nota fiscal da grid de clientes)."""
    query = (
        db.query(NfseNota, Billing)
        .join(Billing, Billing.id == NfseNota.billing_id)
        .filter(Billing.is_deleted.is_(False))
    )
    if client_id:
        query = query.filter(Billing.client_id == client_id)
    rows = query.order_by(NfseNota.id.desc()).limit(limit).all()
    return [
        NfseClientItem(
            **NfseOut.model_validate(nota).model_dump(),
            valor=float(billing.amount) if billing.amount is not None else None,
            titulo=billing.title,
        )
        for nota, billing in rows
    ]


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
        return _provedor().emitir_nfse(db, billing, client)
    except (*_ERROS_CONFIG, *_ERROS_API) as exc:
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
    try:
        # No Emissor Nacional a geração é síncrona: não existe protocolo a
        # reconsultar. Se a nota saiu, revalidamos o XML pela chave de acesso.
        if _provedor() is nfse_nacional:
            if not nota.chave_acesso:
                raise HTTPException(
                    status_code=400,
                    detail='Emissão pelo Emissor Nacional é síncrona — '
                           'não há protocolo a consultar. Emita novamente.',
                )
            nota.xml_retorno = nfse_nacional.consultar_por_chave(nota.chave_acesso)
            db.commit()
            return nota

        if not nota.protocolo:
            raise HTTPException(status_code=400, detail='NFS-e ainda não enviada (sem protocolo)')
        return nfse_joinville.consultar(db, nota)
    except (*_ERROS_CONFIG, *_ERROS_API) as exc:
        _raise_nfse_error(exc)
