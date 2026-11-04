"""Lotes/carnês: fila durável sobre a emissão individual oficial do Sicredi."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import logging
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import session as db_session
from app.models.billing import Billing
from app.models.enums import BillingStatus
from app.models.sicredi_boleto import SicrediBoleto
from app.models.sicredi_lote import SicrediLote
from app.services import sicredi_boletos, sicredi_client, titulo_bancario
from app.services.ailos_boletos import resolver_pagador
from app.services.financial import lock_billings_for_update

logger = logging.getLogger(__name__)


def validar_convenio(lote: SicrediLote) -> None:
    cred = sicredi_client.credenciais()
    if (lote.ambiente, lote.cooperativa, lote.posto, lote.codigo_beneficiario) != (
        cred.ambiente, cred.cooperativa, cred.posto, cred.codigo_beneficiario,
    ):
        raise sicredi_client.SicrediError('O lote pertence a outro ambiente ou convênio Sicredi.')


def criar_lote(db: Session, billing_ids: list[int], *, tipo: str = 'carne') -> SicrediLote:
    cred = sicredi_client.credenciais()
    ids = sorted(set(billing_ids))
    if not ids or len(ids) > 1000 or (tipo == 'carne' and len(ids) < 2):
        raise sicredi_boletos.SicrediValidationError(['Selecione de 2 a 1.000 parcelas para carnê, ou de 1 a 1.000 para lote.'])
    billings = lock_billings_for_update(db, ids)
    if len(billings) != len(ids) or any(b.is_deleted or b.status not in (BillingStatus.PENDING, BillingStatus.OVERDUE) for b in billings):
        raise sicredi_boletos.SicrediValidationError(['O lote contém cobranças removidas, inexistentes, pagas ou canceladas.'])
    titulo_bancario.recusar_somente_sistema(db, ids)
    titulos = titulo_bancario.titulos_bancarios(db, ids)
    registros = {b.billing_id: b for b in db.scalars(select(SicrediBoleto).where(SicrediBoleto.billing_id.in_(ids))).all()}
    for billing in billings:
        titulo = titulos[billing.id]
        registro = registros.get(billing.id)
        if titulo.canal == titulo_bancario.CANAL_SICREDI and registro is not None:
            if registro.baixa_status is not None or not sicredi_boletos._mesmo_convenio(registro, cred):
                raise sicredi_boletos.SicrediValidationError([f'Cobrança #{billing.id} tem título baixado ou de outro convênio Sicredi.'])
        else:
            titulo_bancario.validar_titulos(titulo_bancario.EMITIR_SICREDI, {billing.id: titulo})
    pagadores = {resolver_pagador(db, b).id for b in billings}
    if tipo == 'carne' and len(pagadores) != 1:
        raise sicredi_boletos.SicrediValidationError(['Um carnê deve ter um único responsável financeiro.'])
    contexto = [cred.ambiente, cred.cooperativa, cred.posto, cred.codigo_beneficiario]
    chave = hashlib.sha256(json.dumps([tipo, contexto, ids]).encode()).hexdigest()
    existente = db.scalar(select(SicrediLote).where(SicrediLote.chave == chave))
    if existente:
        db.commit()
        return existente
    lote = SicrediLote(chave=chave, ticket=str(uuid4()), tipo=tipo, ambiente=cred.ambiente,
                      cooperativa=cred.cooperativa, posto=cred.posto, codigo_beneficiario=cred.codigo_beneficiario,
                      payer_client_id=next(iter(pagadores)) if len(pagadores) == 1 else None,
                      billing_ids=ids, status='processing', erros={})
    db.add(lote)
    db.flush()
    for billing in billings:
        registro = registros.get(billing.id)
        if registro is None or titulo_bancario.estado_do_sicredi(registro) == titulo_bancario.SEM_TITULO:
            registro = sicredi_boletos.preparar_reserva_lote(db, billing, cred, lote_id=lote.id)
            registro.status = 'NA_FILA'
            registro.registro_iniciado_em = None
    db.commit()
    db.refresh(lote)
    return lote


def status_lote(db: Session, lote: SicrediLote) -> dict:
    ids = lote.billing_ids or []
    cobrancas = {b.id: b for b in db.scalars(select(Billing).where(Billing.id.in_(ids))).all()}
    registros = {b.billing_id: b for b in db.scalars(select(SicrediBoleto).where(SicrediBoleto.billing_id.in_(ids))).all()}
    parcelas = []
    for indice, bid in enumerate(ids, 1):
        b, registro = cobrancas.get(bid), registros.get(bid)
        estado = titulo_bancario.estado_do_sicredi(registro)
        erro = (lote.erros or {}).get(str(bid))
        if b is None or b.is_deleted or b.status == BillingStatus.CANCELED:
            situacao, erro = 'erro', 'Cobrança removida, paga ou cancelada.'
        elif registro is not None and registro.baixa_status is not None:
            situacao, erro = 'erro', 'Título com baixa pendente ou confirmada.'
        elif estado == titulo_bancario.REGISTRADO:
            # Pagamento posterior não desfaz o registro nem exige outro POST.
            situacao, erro = 'registrado', None
        elif b.status == BillingStatus.PAID:
            situacao, erro = 'erro', 'Cobrança já paga; nenhuma emissão será realizada.'
        elif estado in (titulo_bancario.EM_REGISTRO, titulo_bancario.DESFECHO_DESCONHECIDO):
            situacao = 'processando'
            erro = erro or 'Registro ainda não confirmado. A nova tentativa consulta o banco antes de reenviar.'
        else:
            erro = erro or (registro.erro if registro else None)
            situacao = 'erro' if erro else 'processando'
        parcelas.append({'billing_id': bid, 'numero_parcela': indice,
                         'vencimento': b.due_date if b else None,
                         'valor': float(b.amount) if b else None, 'status': situacao, 'erro': erro,
                         'nosso_numero': registro.nosso_numero if registro else None,
                         'linha_digitavel': registro.linha_digitavel if registro else None})
    prontas = sum(p['status'] == 'registrado' for p in parcelas)
    return {'ticket': lote.ticket, 'lote_id': lote.id, 'total': len(ids), 'prontas': prontas,
            'status': 'completed' if prontas == len(ids) else lote.status if lote.status != 'completed' else 'partial',
            'parcelas': parcelas}


@contextmanager
def _trava_processamento(db: Session, lote_id: int):
    """Lock dedicado sobrevive aos commits de cada boleto e não segura Billing na rede."""
    if db.get_bind().dialect.name != 'postgresql':
        yield True
        return
    conn = db.get_bind().connect()
    obtido = False
    try:
        obtido = bool(conn.execute(text('SELECT pg_try_advisory_lock(918274, :id)'), {'id': lote_id}).scalar())
        yield obtido
    finally:
        if obtido:
            conn.execute(text('SELECT pg_advisory_unlock(918274, :id)'), {'id': lote_id})
        conn.close()


def processar_lote(db: Session, lote_id: int, *, billing_ids: list[int] | None = None,
                   retentar: bool = False, limite: int | None = None) -> dict:
    lote = db.get(SicrediLote, lote_id)
    if lote is None:
        raise sicredi_client.SicrediError('Lote Sicredi não encontrado.')
    validar_convenio(lote)
    ids = list(lote.billing_ids or []) if billing_ids is None else sorted(set(billing_ids))
    if not set(ids).issubset(lote.billing_ids or []):
        raise sicredi_client.SicrediError('A parcela não pertence ao lote.')
    with _trava_processamento(db, lote_id) as obtido:
        if not obtido:
            return status_lote(db, lote)
        lote.status = 'processing'
        db.commit()
        tentativas = 0
        for bid in ids:
            db.refresh(lote)
            registro = sicredi_boletos.boleto_da_cobranca(db, bid, recarregar=True)
            cobranca = db.get(Billing, bid, populate_existing=True)
            pronta = (registro is not None and titulo_bancario.estado_do_sicredi(registro) == titulo_bancario.REGISTRADO
                      and registro.baixa_status is None and cobranca is not None and not cobranca.is_deleted
                      and cobranca.status in (BillingStatus.PENDING, BillingStatus.OVERDUE, BillingStatus.PAID))
            if pronta or (not retentar and str(bid) in (lote.erros or {})):
                continue
            if limite is not None and tentativas >= limite:
                break
            tentativas += 1
            erro = None
            try:
                sicredi_boletos.gerar_boleto(db, bid, nova_tentativa=True, lote_id=lote.id)
            except Exception as exc:  # um erro não interrompe as outras parcelas
                db.rollback()
                if isinstance(exc, sicredi_boletos.SicrediValidationError):
                    erro = '; '.join(exc.errors)
                elif isinstance(exc, sicredi_client.SicrediApiError):
                    erro = exc.mensagem or exc.friendly_message
                else:
                    erro = str(exc)
                logger.warning('Lote Sicredi %s: parcela #%s não confirmada: %s', lote_id, bid, erro)
            lote = db.get(SicrediLote, lote_id, populate_existing=True)
            erros = dict(lote.erros or {})
            if erro:
                erros[str(bid)] = erro[:2000]
            else:
                erros.pop(str(bid), None)
            lote.erros = erros
            db.commit()
        resumo = status_lote(db, lote)
        sem_tentativa = any(p['status'] != 'registrado' and str(p['billing_id']) not in (lote.erros or {})
                           for p in resumo['parcelas'])
        lote.status = 'completed' if resumo['prontas'] == resumo['total'] else 'processing' if sem_tentativa else 'partial'
        db.commit()
        return status_lote(db, lote)


def processar_em_background(lote_id: int, retentar: bool = False) -> None:
    db = db_session.SessionLocal()
    try:
        processar_lote(db, lote_id, retentar=retentar)
    except Exception:
        db.rollback()
        logger.exception('Lote Sicredi %s permanece disponível para retomada.', lote_id)
    finally:
        db.close()


def processar_fila(db: Session) -> None:
    """Recupera lotes interrompidos por reinício do processo/servidor."""
    if not sicredi_client.habilitado():
        return
    cred = sicredi_client.credenciais()
    ids = db.scalars(select(SicrediLote.id).where(
        SicrediLote.status == 'processing', SicrediLote.ambiente == cred.ambiente,
        SicrediLote.cooperativa == cred.cooperativa, SicrediLote.posto == cred.posto,
        SicrediLote.codigo_beneficiario == cred.codigo_beneficiario,
    ).order_by(SicrediLote.updated_at, SicrediLote.id).limit(3)).all()
    for lote_id in ids:
        processar_lote(db, lote_id, limite=25)
