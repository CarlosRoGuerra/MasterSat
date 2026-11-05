"""Canal Pix Automático: seleção não cria consentimento nem débito no banco."""
from __future__ import annotations

from secrets import compare_digest
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.v1.endpoints.common import get_client_or_404
from app.core.config import settings
from app.db.session import get_db
from app.models.billing import Billing
from app.models.enums import BillingStatus, UserRole
from app.models.sicredi_pix import SicrediPixCobranca, SicrediPixRecorrencia
from app.schemas.sicredi_pix import PixAutorizacaoIn, PixClienteOut, PixCobrancaOut, PixRecorrenciaOut, PixStatusOut
from app.services import sicredi_pix as service
from app.services import sicredi_pix_client as client
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
    pending = db.scalar(select(func.count()).select_from(Billing).where(
        func.coalesce(Billing.payer_client_id, Billing.client_id) == client_id,
        Billing.payment_method == 'pix_automatico', Billing.is_deleted.is_(False),
        Billing.status.in_((BillingStatus.PENDING, BillingStatus.OVERDUE)),
        ~Billing.id.in_(select(SicrediPixCobranca.billing_id)),
    )) or 0
    return PixClienteOut(client_id=client_id, forma_cobranca=payer.forma_cobranca, conexao=_status(),
                         recorrencia=rec_out, cobrancas=charges, pendentes_adesao=pending)


@router.get('/status', response_model=PixStatusOut)
def status(_: object = Depends(require_roles(*FINANCE))):
    return _status()


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
        service.criar_recorrencia(db, client_id, **payload.model_dump())
    except client.PixError as exc:
        _raise(exc)
    return _cliente(db, client_id)


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
    ids = db.scalars(select(Billing.id).where(
        func.coalesce(Billing.payer_client_id, Billing.client_id) == client_id,
        Billing.payment_method == 'pix_automatico', Billing.is_deleted.is_(False),
        Billing.status.in_((BillingStatus.PENDING, BillingStatus.OVERDUE)),
    ).order_by(Billing.id).with_for_update()).all()
    service.enfileirar_fechamento(db, list(ids))
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
