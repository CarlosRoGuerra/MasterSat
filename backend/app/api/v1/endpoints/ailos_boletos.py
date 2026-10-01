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
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.v1.endpoints.ailos_common import ALLOWED_ROLES, raise_ailos_error
from app.api.v1.endpoints.common import (
    get_billing_or_404 as _get_billing_or_404,
    get_client_or_404 as _get_client_or_404,
)
from app.db.session import get_db
from app.models.ailos_boleto import AilosBoleto
from app.models.ailos_lote import AilosLote
from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import UserRole
from app.schemas.ailos import (
    AilosBoletoOut,
    AilosDesfechoOut,
    AilosGerarBoletoIn,
    AilosGerarLoteIn,
    AilosJustificativaIn,
    AilosLoteOut,
    AilosLoteStatusOut,
    AilosPagamentoOut,
    AilosPendenciaOut,
)
from app.services.ailos_boletos import (
    confirmar_baixa_manual,
    consultar_boleto,
    consultar_lote,
    declarar_nao_registrado,
    gerar_boleto,
    gerar_boleto_lote,
    gerar_carne_lote,
    metricas_conciliacao,
    parcelas_do_lote,
    registrar_parcela_individual,
    registrar_pendentes_do_lote,
    resolver_desfecho,
    resolver_pendencia,
    verificar_pagamento,
)
from app.services import titulo_bancario
from app.services.ailos_client import AilosApiError, AilosError
from app.services.ailos_validators import AilosValidationError
from app.services.titulo_bancario import PoliticaBancariaError

router = APIRouter()

_AILOS_EXCEPTIONS = (AilosValidationError, AilosError, AilosApiError, PoliticaBancariaError)


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
# POST /ailos/boletos/{billing_id}/verificar-pagamento — consulta + baixa
# ---------------------------------------------------------------------------

@router.post('/boletos/{billing_id}/verificar-pagamento', response_model=AilosPagamentoOut)
def verificar_pagamento_endpoint(
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Consulta o boleto na Ailos e, se estiver pago, dá baixa na cobrança."""
    billing = _get_billing_or_404(billing_id, db)
    try:
        result = verificar_pagamento(db, billing)
    except _AILOS_EXCEPTIONS as exc:
        raise_ailos_error(exc)
    return AilosPagamentoOut(billing_id=billing_id, **result)


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
    # Um carnê é sempre de UM cliente — as parcelas são as prestações dele. Misturar
    # clientes num mesmo carnê não faz sentido (e gera boletos cruzados na Ailos).
    if len({b.client_id for b in billings}) > 1:
        raise HTTPException(
            status_code=400,
            detail='Um carnê deve ser de um único cliente. As parcelas selecionadas '
                   'pertencem a clientes diferentes.',
        )
    if len(billings) < 2:
        raise HTTPException(status_code=400, detail='Um carnê precisa de ao menos 2 parcelas.')
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
    """Consulta o status de um lote/carnê. Se concluído, atualiza os boletos
    correspondentes. Também é o endpoint usado para "verificar/tentar
    novamente" manualmente na tela de acompanhamento do carnê."""
    lote = db.query(AilosLote).filter_by(ticket=ticket).first()
    if lote is None:
        raise HTTPException(status_code=404, detail="Lote não encontrado")

    status_atual = lote.status
    if status_atual == 'processing':
        try:
            result = consultar_lote(db, lote)
        except _AILOS_EXCEPTIONS as exc:
            raise_ailos_error(exc)
        status_atual = result.get('status', 'processing')

    # Consultado sempre — inclusive enquanto ainda 'processing' — para a tela
    # mostrar "3 de 12 confirmadas" em vez de um spinner sem informação, já
    # que cada consultar_lote() acima atualiza as parcelas que resolveram
    # mesmo sem fechar o lote inteiro ainda.
    boletos = db.query(AilosBoleto).filter_by(lote_id=lote.id).all()
    prontas = sum(1 for b in boletos if b.linha_digitavel)
    return AilosLoteStatusOut(
        ticket=lote.ticket,
        status=status_atual,
        lote_id=lote.id,
        boletos=[AilosBoletoOut.model_validate(b) for b in boletos] if status_atual != 'processing' else None,
        total=len(lote.billing_ids or []),
        prontas=prontas,
        parcelas=parcelas_do_lote(db, lote),
    )


# ---------------------------------------------------------------------------
# POST /ailos/lotes/{lote_id}/parcelas/{billing_id}/registrar — retry individual
# ---------------------------------------------------------------------------

@router.post('/lotes/{lote_id}/parcelas/{billing_id}/registrar', response_model=AilosBoletoOut)
def registrar_parcela_endpoint(
    lote_id: int,
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Tenta registrar (ou recuperar) uma única parcela de um lote/carnê que
    ainda não confirmou — reaproveita ``gerar_boleto`` (idempotente, não cria
    Billing novo) e mantém a parcela associada a este lote."""
    lote = db.get(AilosLote, lote_id)
    if lote is None:
        raise HTTPException(status_code=404, detail='Lote não encontrado')
    try:
        return registrar_parcela_individual(db, lote, billing_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except _AILOS_EXCEPTIONS as exc:
        raise_ailos_error(exc)


# ---------------------------------------------------------------------------
# POST /ailos/lotes/{lote_id}/registrar-pendentes — retry em massa
# ---------------------------------------------------------------------------

@router.post('/lotes/{lote_id}/registrar-pendentes')
def registrar_pendentes_endpoint(
    lote_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """"Gerar boletos pendentes": tenta registrar de uma vez todas as
    parcelas do lote que ainda não confirmaram. Uma falha pontual numa
    parcela não impede as demais de serem tentadas."""
    lote = db.get(AilosLote, lote_id)
    if lote is None:
        raise HTTPException(status_code=404, detail='Lote não encontrado')
    return registrar_pendentes_do_lote(db, lote)


# ---------------------------------------------------------------------------
# Desfecho desconhecido, baixa e pendências (Fase 03 — FIN-02/03/05/06)
# ---------------------------------------------------------------------------

@router.post('/boletos/{billing_id}/consultar-desfecho', response_model=AilosDesfechoOut)
def consultar_desfecho_endpoint(
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Consulta a Ailos pelo número do documento para resolver um registro
    com desfecho desconhecido (timeout/5xx/reserva órfã). Não reenvia nada."""
    _get_billing_or_404(billing_id, db)
    return AilosDesfechoOut(billing_id=billing_id, **resolver_desfecho(db, billing_id))


@router.post('/boletos/{billing_id}/declarar-nao-registrado', response_model=AilosDesfechoOut)
def declarar_nao_registrado_endpoint(
    billing_id: int,
    payload: AilosJustificativaIn,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.ADMIN)),
):
    """Saída manual (só administrador, com justificativa no histórico) quando
    a consulta não resolve e foi conferido no banco que o título não existe."""
    _get_billing_or_404(billing_id, db)
    try:
        declarar_nao_registrado(db, billing_id, user_id=current_user.id, justificativa=payload.justificativa.strip())
    except PoliticaBancariaError as exc:
        raise_ailos_error(exc)
    return AilosDesfechoOut(
        billing_id=billing_id, resultado='nao_registrado', estado=titulo_bancario.SEM_TITULO,
        mensagem='Registro declarado como não realizado; a cobrança volta a aceitar emissão.',
    )


@router.post('/boletos/{billing_id}/confirmar-baixa', response_model=AilosBoletoOut)
def confirmar_baixa_endpoint(
    billing_id: int,
    payload: AilosJustificativaIn,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.ADMIN)),
):
    """Administrador confirma que baixou o título no internet banking. O
    caminho normal é automático: a conciliação confirma quando a consulta
    devolve situação 3 (baixado)."""
    try:
        return confirmar_baixa_manual(db, billing_id, user_id=current_user.id, justificativa=payload.justificativa.strip())
    except PoliticaBancariaError as exc:
        raise_ailos_error(exc)


@router.post('/boletos/{billing_id}/resolver-pendencia', response_model=AilosBoletoOut)
def resolver_pendencia_endpoint(
    billing_id: int,
    payload: AilosJustificativaIn,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*ALLOWED_ROLES)),
):
    """Encerra uma pendência de conciliação já tratada, com justificativa."""
    try:
        return resolver_pendencia(db, billing_id, user_id=current_user.id, justificativa=payload.justificativa.strip())
    except PoliticaBancariaError as exc:
        raise_ailos_error(exc)


@router.get('/conciliacao')
def conciliacao_status(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Métricas da conciliação: carteira monitorada, atraso da consulta mais
    antiga, janela estimada, desfechos desconhecidos, baixas e pendências."""
    return metricas_conciliacao(db)


@router.get('/pendencias', response_model=list[AilosPendenciaOut])
def listar_pendencias(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """Tudo que precisa de ação humana: desfecho desconhecido, baixa pendente
    no banco e divergência de pagamento — mais antigos primeiro."""
    rows = (
        db.query(AilosBoleto, Billing, Client.name)
        .join(Billing, Billing.id == AilosBoleto.billing_id)
        .outerjoin(Client, Client.id == func.coalesce(Billing.payer_client_id, Billing.client_id))
        .filter(or_(
            AilosBoleto.pendencia.isnot(None),
            AilosBoleto.baixa_status == 'pendente',
            AilosBoleto.status_ailos == titulo_bancario.STATUS_DESFECHO_DESCONHECIDO,
            AilosBoleto.status_ailos.in_(titulo_bancario.STATUS_EM_REGISTRO),
        ))
        .order_by(AilosBoleto.id.asc())
        .limit(1000)
        .all()
    )
    resultado = []
    for boleto, billing, cliente in rows:
        estado = titulo_bancario.estado_do_boleto(boleto)
        if estado == titulo_bancario.EM_REGISTRO and not boleto.pendencia and boleto.baixa_status != 'pendente':
            continue  # registro recente, ainda dentro do prazo normal
        resultado.append(AilosPendenciaOut(
            billing_id=billing.id,
            nosso_numero=boleto.nosso_numero,
            status_ailos=boleto.status_ailos,
            estado=estado,
            baixa_status=boleto.baixa_status,
            baixa_solicitada_em=boleto.baixa_solicitada_em,
            pendencia=boleto.pendencia,
            pendencia_detalhe=boleto.pendencia_detalhe,
            pendencia_desde=boleto.pendencia_desde,
            ultima_consulta_em=boleto.ultima_consulta_em,
            ultima_consulta_erro=boleto.ultima_consulta_erro,
            billing_status=billing.status.value,
            billing_removida=bool(billing.is_deleted),
            valor=float(billing.amount),
            vencimento=billing.due_date,
            cliente=cliente,
        ))
    return resultado
