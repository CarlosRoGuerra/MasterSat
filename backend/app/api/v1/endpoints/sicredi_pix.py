"""Canal Pix Automático: seleção não cria consentimento nem débito no banco."""
from __future__ import annotations

from datetime import timedelta
from secrets import compare_digest
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.v1.endpoints.common import get_client_or_404
from app.core.config import settings
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.sicredi_pix import SicrediPixCobranca, SicrediPixRecorrencia
from app.schemas.sicredi_pix import (
    PixAutorizacaoIn, PixClienteOut, PixCobrancaOut, PixPainelOut, PixRecorrenciaOut, PixStatusOut,
)
from app.services import sicredi_pix as service
from app.services import sicredi_pix_client as client
from app.services import sicredi_pix_convite as convite
from app.services.sicredi_pix_painel import montar_painel
from app.services.titulo_bancario import PoliticaBancariaError

router = APIRouter()
FINANCE = (UserRole.ADMIN, UserRole.FINANCIAL)


def _raise(exc):
    if isinstance(exc, PoliticaBancariaError):
        raise HTTPException(409, detail=exc.detail()) from exc
    status = 502 if exc.status_code and exc.status_code >= 500 else 409
    raise HTTPException(status, detail=client.erro_seguro(str(exc))) from exc


def _status() -> PixStatusOut:
    configured = False
    error = None
    try:
        client.validar_configuracao()
        configured = True
    except client.PixError as exc:
        error = str(exc)
    env = settings.sicredi_pix_ambiente
    writes = (configured and settings.sicredi_pix_escrita_habilitada
              and (env != 'producao' or settings.sicredi_pix_producao_confirmada))
    return PixStatusOut(habilitado=settings.sicredi_pix_enabled, configurado=configured,
        ambiente=env, escrita_habilitada=writes, erro_configuracao=error)


def _qr_svg(value: str) -> str:
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    qr = QrCodeWidget(value, barLevel='M')
    x1, y1, x2, y2 = qr.getBounds()
    size = 240
    drawing = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    drawing.add(qr)
    return renderSVG.drawToString(drawing)


def _cliente(db: Session, client_id: int) -> PixClienteOut:
    payer = get_client_or_404(client_id, db)
    rec = service.recorrencia_cliente(db, client_id)
    rec_out = PixRecorrenciaOut.model_validate(rec) if rec else None
    if rec_out and rec.pix_copia_cola and rec.status == 'CRIADA':
        rec_out.qr_svg = _qr_svg(rec.pix_copia_cola)
    charges = db.scalars(select(SicrediPixCobranca).join(SicrediPixRecorrencia).where(
        SicrediPixRecorrencia.client_id == client_id, SicrediPixRecorrencia.ambiente == client.ambiente(),
    ).order_by(SicrediPixCobranca.id.desc()).limit(50)).all()
    pending = db.scalar(select(func.count()).select_from(
        service.cobrancas_pix_sem_agendamento(client_id).subquery())) or 0
    return PixClienteOut(client_id=client_id, forma_cobranca=payer.forma_cobranca, conexao=_status(),
                         recorrencia=rec_out, cobrancas=charges, pendentes_adesao=pending,
                         primeiro_vencimento_sugerido=service.sugerir_inicio_autorizacao(db, payer),
                         data_minima_autorizacao=service.hoje() + timedelta(days=2),
                         smtp_configurado=convite.smtp_configurado(db),
                         emails_responsavel=convite.destinatarios(db, client_id))


@router.get('/status', response_model=PixStatusOut)
def status(db: Session = Depends(get_db), _: object = Depends(require_roles(*FINANCE))):
    out = _status()
    out.smtp_configurado = convite.smtp_configurado(db)
    return out


@router.post('/testar-conexao')
def testar_conexao(_: object = Depends(require_roles(UserRole.ADMIN))):
    try:
        with client.PixClient() as api:
            api.token()
    except client.PixError as exc:
        _raise(exc)
    return {'ok': True, 'ambiente': client.ambiente(), 'somente_leitura': True}


@router.get('/clientes/{client_id}', response_model=PixClienteOut)
def cliente(client_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(*FINANCE))):
    return _cliente(db, client_id)


@router.post('/clientes/{client_id}/autorizacao', response_model=PixClienteOut)
def autorizar(client_id: int, payload: PixAutorizacaoIn, db: Session = Depends(get_db),
              _: object = Depends(require_roles(*FINANCE))):
    get_client_or_404(client_id, db)
    try:
        rec = service.criar_recorrencia(db, client_id, **payload.model_dump())
    except client.PixError as exc:
        _raise(exc)
    # Depois da autorização persistida (sem transação aberta durante o SMTP):
    # envia o convite pedido e associa cobranças do fechamento se já aprovada.
    # Falhas ficam registradas na autorização e aparecem na tela e no painel.
    if rec.convite_status == convite.PENDENTE:
        try:
            convite.enviar_convite(db, rec.id)
        except client.PixError:
            db.rollback()
    if rec.ativa and rec.status == 'APROVADA':
        service.enfileirar_pendentes(db, client_id)
        db.commit()
    return _cliente(db, client_id)


@router.post('/clientes/{client_id}/convite', response_model=PixClienteOut)
def enviar_convite(client_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(*FINANCE))):
    """Envia (ou reenvia) por e-mail o QR da MESMA autorização: nenhuma chamada ao banco."""
    get_client_or_404(client_id, db)
    rec = service.recorrencia_cliente(db, client_id)
    if not rec:
        raise HTTPException(404, detail='Cliente ainda não tem autorização Pix.')
    try:
        convite.enviar_convite(db, rec.id, manual=True)
    except client.PixError as exc:
        db.rollback()
        raise HTTPException(409, detail=client.erro_seguro(str(exc))) from exc
    return _cliente(db, client_id)


@router.get('/painel', response_model=PixPainelOut)
def painel(incluir_resolvidos: bool = Query(default=False), db: Session = Depends(get_db),
           _: object = Depends(require_roles(*FINANCE))):
    """Situação Pix por cliente/cobrança e alertas para o Financeiro (somente leitura)."""
    status_ = _status()
    return {**montar_painel(db, incluir_resolvidos=incluir_resolvidos),
            'habilitado': status_.habilitado, 'escrita_habilitada': status_.escrita_habilitada}


@router.post('/clientes/{client_id}/consultar', response_model=PixClienteOut)
def consultar(client_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(*FINANCE))):
    get_client_or_404(client_id, db)
    rec = service.recorrencia_cliente(db, client_id)
    if not rec:
        raise HTTPException(404, detail='Cliente ainda não tem autorização Pix.')
    try:
        service.consultar_recorrencia(db, rec)
    except client.PixError as exc:
        db.rollback()
        _raise(exc)
    return _cliente(db, client_id)


@router.post('/clientes/{client_id}/cancelar', response_model=PixClienteOut)
def cancelar(client_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(*FINANCE))):
    get_client_or_404(client_id, db)
    rec = service.recorrencia_cliente(db, client_id)
    if not rec:
        raise HTTPException(404, detail='Cliente ainda não tem autorização Pix.')
    try:
        service.cancelar_recorrencia(db, rec)
    except client.PixError as exc:
        db.rollback()
        _raise(exc)
    return _cliente(db, client_id)


@router.post('/clientes/{client_id}/preparar-pendentes', response_model=PixClienteOut)
def preparar_pendentes(client_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(*FINANCE))):
    get_client_or_404(client_id, db)
    service.enfileirar_pendentes(db, client_id)
    db.commit()
    return _cliente(db, client_id)


def _cobranca(db: Session, charge_id: int):
    charge = db.get(SicrediPixCobranca, charge_id)
    if not charge:
        raise HTTPException(404, detail='Agendamento Pix não encontrado.')
    return charge


@router.post('/cobrancas/{charge_id}/consultar', response_model=PixCobrancaOut)
def consultar_cobranca(charge_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(*FINANCE))):
    charge = _cobranca(db, charge_id)
    try:
        service.consultar_cobranca(db, charge)
    except client.PixError as exc:
        db.rollback()
        _raise(exc)
    return charge


@router.post('/cobrancas/{charge_id}/cancelar', response_model=PixCobrancaOut)
def cancelar_cobranca(charge_id: int, db: Session = Depends(get_db), _: object = Depends(require_roles(*FINANCE))):
    charge = _cobranca(db, charge_id)
    try:
        service.cancelar_cobranca(db, charge)
    except client.PixError as exc:
        db.rollback()
        _raise(exc)
    return charge


@router.post('/processar-fila')
def processar_fila(db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN))):
    return service.processar_fila(db)


@router.post('/webhooks/configurar')
def configurar_webhooks(_: object = Depends(require_roles(UserRole.ADMIN))):
    token = settings.sicredi_pix_webhook_token
    base = settings.backend_public_url.rstrip('/')
    url = urlsplit(base)
    if len(token) < 32 or not token.isalnum() or url.scheme != 'https' or url.port not in (None, 443):
        raise HTTPException(409, detail='Configure URL pública HTTPS e segredo de webhook com ao menos 32 caracteres alfanuméricos.')
    target = base + settings.api_v1_prefix + '/sicredi-pix/webhooks/' + token
    try:
        with client.PixClient(escrita=True) as api:
            for endpoint in ('/api/v1/webhookrec', '/api/v1/webhookcobr'):
                api.request('PUT', endpoint, json={'webhookUrl': target})
            if settings.sicredi_pix_chave:
                api.request('PUT', '/api/v2/webhook/' + settings.sicredi_pix_chave, json={'webhookUrl': target})
    except client.PixError as exc:
        _raise(exc)
    return {'ok': True}


@router.post('/webhooks/{token}/{tipo}', include_in_schema=False)
@router.post('/webhooks/{token}/{tipo}/', include_in_schema=False)
def webhook(token: str, tipo: str, payload: dict, db: Session = Depends(get_db)):
    expected = settings.sicredi_pix_webhook_token
    if (not settings.sicredi_pix_enabled or len(expected) < 32 or not compare_digest(token.encode('utf-8'), expected.encode('utf-8'))
        or tipo not in ('pix', 'rec', 'cobr')):
        raise HTTPException(404, detail='Não encontrado')
    try:
        service.receber_aviso(db, payload)
    except client.PixError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return {'ok': True}
