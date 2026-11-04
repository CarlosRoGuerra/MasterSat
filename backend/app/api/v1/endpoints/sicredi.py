"""Endpoints da API de Cobrança Sicredi.

GET  /sicredi/status                         → configuração (sem rede)
POST /sicredi/testar-conexao                 → autentica no ambiente configurado (ADMIN)
POST /sicredi/boletos                        → registra o boleto de uma cobrança
GET  /sicredi/boletos/{billing_id}           → registro local do boleto
POST /sicredi/boletos/{billing_id}/consultar → consulta e aplica (pagamento, baixa, desfecho)
GET  /sicredi/boletos/{billing_id}/pdf       → PDF MasterSat com dados registrados no Sicredi
POST /sicredi/boletos/{billing_id}/baixa     → instrução de baixa pela API
POST /sicredi/conciliar                      → uma rodada de conciliação agora
"""
from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.v1.endpoints.common import get_billing_or_404
from app.core.config import settings
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.sicredi_boleto import SicrediBoleto
from app.models.user import User
from app.schemas.sicredi import (
    SicrediBaixaIn,
    SicrediBaixaOut,
    SicrediBoletoOut,
    SicrediConciliacaoOut,
    SicrediConexaoOut,
    SicrediConsultaOut,
    SicrediEmitirIn,
    SicrediStatusOut,
)
from app.services import sicredi_boletos, sicredi_client, titulo_bancario
from app.services.sicredi_nosso_numero import NossoNumeroError
from app.services.titulo_bancario import PoliticaBancariaError

router = APIRouter()

ALLOWED_ROLES = (UserRole.ADMIN, UserRole.FINANCIAL)
_EXCECOES = (
    sicredi_boletos.SicrediValidationError,
    sicredi_client.SicrediError,
    sicredi_client.SicrediApiError,
    PoliticaBancariaError,
    NossoNumeroError,
)


def raise_sicredi_error(exc: Exception) -> NoReturn:
    if isinstance(exc, sicredi_boletos.SicrediValidationError):
        raise HTTPException(status_code=422, detail={'errors': exc.errors}) from exc
    if isinstance(exc, PoliticaBancariaError):
        raise HTTPException(status_code=409, detail=exc.detail()) from exc
    if isinstance(exc, sicredi_client.SicrediDesfechoDesconhecido):
        # Não é "tente de novo": o banco pode ter registrado.
        raise HTTPException(status_code=502, detail={
            'code': 'desfecho_desconhecido',
            'message': str(exc),
            'billing_ids': getattr(exc, 'billing_ids', []),
        }) from exc
    if isinstance(exc, sicredi_client.SicrediApiError):
        raise HTTPException(status_code=502, detail={
            'friendly_message': exc.friendly_message,
            'sicredi_message': exc.mensagem,
            'sicredi_code': exc.codigo,
            'status_code': exc.status_code,
        }) from exc
    if isinstance(exc, (sicredi_client.SicrediError, NossoNumeroError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


def _boleto_out(boleto: SicrediBoleto) -> SicrediBoletoOut:
    return SicrediBoletoOut(
        billing_id=boleto.billing_id,
        estado=titulo_bancario.estado_do_sicredi(boleto),
        status=boleto.status,
        ambiente=boleto.ambiente,
        situacao=boleto.situacao,
        nosso_numero=boleto.nosso_numero,
        seu_numero=boleto.seu_numero,
        tipo_cobranca=boleto.tipo_cobranca,
        linha_digitavel=boleto.linha_digitavel,
        codigo_barras=boleto.codigo_barras,
        txid=boleto.txid,
        qr_code=boleto.qr_code,
        valor_nominal=float(boleto.valor_nominal) if boleto.valor_nominal is not None else None,
        data_vencimento=boleto.data_vencimento,
        erro=boleto.erro,
        baixa_status=boleto.baixa_status,
        baixa_enviada_em=boleto.baixa_enviada_em,
        baixa_confirmada_em=boleto.baixa_confirmada_em,
        pendencia=boleto.pendencia,
        ultima_consulta_em=boleto.ultima_consulta_em,
        data_liquidacao=boleto.data_liquidacao,
        valor_liquidado=float(boleto.valor_liquidado) if boleto.valor_liquidado is not None else None,
    )


@router.get('/status', response_model=SicrediStatusOut)
def status_endpoint(_: object = Depends(require_roles(*ALLOWED_ROLES))):
    """Como o canal está configurado (não chama o Sicredi)."""
    base = {
        'habilitado': sicredi_client.habilitado(),
        'ambiente': sicredi_client.ambiente_configurado(),
        'producao_confirmada': bool(settings.sicredi_producao_confirmada),
        'hibrido': bool(settings.sicredi_boleto_hibrido),
    }
    if not base['habilitado']:
        return SicrediStatusOut(**base)
    try:
        cred = sicredi_client.credenciais()
    except sicredi_client.SicrediError as exc:
        return SicrediStatusOut(**base, erro_configuracao=str(exc))
    return SicrediStatusOut(
        **base, cooperativa=cred.cooperativa, posto=cred.posto, codigo_beneficiario=cred.codigo_beneficiario,
    )


@router.post('/testar-conexao', response_model=SicrediConexaoOut)
def testar_conexao_endpoint(_: object = Depends(require_roles(UserRole.ADMIN))):
    """Autentica no ambiente configurado — nenhum boleto é criado."""
    try:
        dados = sicredi_client.testar_conexao()
    except _EXCECOES as exc:
        raise_sicredi_error(exc)
    return SicrediConexaoOut(ok=True, **dados)


@router.post('/boletos', response_model=SicrediBoletoOut)
def emitir_endpoint(
    payload: SicrediEmitirIn,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    billing = get_billing_or_404(payload.billing_id, db)
    try:
        boleto = sicredi_boletos.gerar_boleto(db, billing.id, nova_tentativa=payload.nova_tentativa)
    except _EXCECOES as exc:
        db.rollback()
        raise_sicredi_error(exc)
    return _boleto_out(boleto)


@router.get('/boletos/{billing_id}', response_model=SicrediBoletoOut)
def detalhe_endpoint(
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    boleto = sicredi_boletos.boleto_da_cobranca(db, billing_id)
    if boleto is None:
        raise HTTPException(status_code=404, detail='Esta cobrança não tem boleto no Sicredi.')
    return _boleto_out(boleto)


@router.post('/boletos/{billing_id}/consultar', response_model=SicrediConsultaOut)
def consultar_endpoint(
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    get_billing_or_404(billing_id, db)
    try:
        resultado = sicredi_boletos.consultar(db, billing_id)
    except _EXCECOES as exc:
        db.rollback()
        raise_sicredi_error(exc)
    return SicrediConsultaOut(billing_id=billing_id, **resultado)


@router.get('/boletos/{billing_id}/pdf')
def pdf_endpoint(
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    from app.api.v1.endpoints.boletos import _pagador_do_billing, _pdf_sicredi

    billing = get_billing_or_404(billing_id, db)
    conteudo, nome = _pdf_sicredi(billing, _pagador_do_billing(billing, db), db)
    return Response(
        content=conteudo, media_type='application/pdf',
        headers={'Content-Disposition': f'inline; filename="{nome}"'},
    )


@router.post('/boletos/{billing_id}/baixa', response_model=SicrediBaixaOut)
def baixa_endpoint(
    billing_id: int,
    payload: SicrediBaixaIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*ALLOWED_ROLES)),
):
    get_billing_or_404(billing_id, db)
    try:
        resultado = sicredi_boletos.solicitar_baixa(db, billing_id, user_id=user.id, motivo=payload.motivo)
    except _EXCECOES as exc:
        db.rollback()
        raise_sicredi_error(exc)
    return SicrediBaixaOut(billing_id=billing_id, **resultado)


@router.post('/boletos/{billing_id}/resolver-pendencia', response_model=SicrediBoletoOut)
def resolver_pendencia_endpoint(
    billing_id: int,
    payload: SicrediBaixaIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Encerra a pendência de conciliação (o ``motivo`` vai para o histórico)."""
    get_billing_or_404(billing_id, db)
    try:
        boleto = sicredi_boletos.resolver_pendencia(db, billing_id, user_id=user.id, justificativa=payload.motivo)
    except _EXCECOES as exc:
        db.rollback()
        raise_sicredi_error(exc)
    return _boleto_out(boleto)


@router.post('/conciliar', response_model=SicrediConciliacaoOut)
def conciliar_endpoint(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    try:
        metricas = sicredi_boletos.conciliar(db)
    except _EXCECOES as exc:
        db.rollback()
        raise_sicredi_error(exc)
    return SicrediConciliacaoOut(**metricas)
