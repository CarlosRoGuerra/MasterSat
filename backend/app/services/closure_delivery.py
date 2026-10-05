"""Conferência e envio por lote de fechamento, sem depender da paginação da carteira."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing, CONSOLIDATED_BILLING_TYPE
from app.models.client import Client
from app.models.closure_email_delivery import ClosureEmailDelivery
from app.models.closure_job import ClosureJob
from app.models.enums import BillingStatus
from app.models.nfse_nota import NfseNota
from app.services.ailos_boletos import resolver_pagador
from app.services.email_smtp import emails_cadastrados


def _lote(db: Session, lote_id: int) -> tuple[ClosureJob, list[int]]:
    lote = db.get(ClosureJob, lote_id)
    ids = (lote.result or {}).get('payment_billing_ids') if lote else None
    if not lote or lote.status != 'completed' or not isinstance(ids, list):
        raise HTTPException(status_code=404, detail='Lote de fechamento não encontrado.')
    return lote, [int(billing_id) for billing_id in ids]


def listar_lotes(db: Session) -> list[dict]:
    lotes = db.query(ClosureJob).filter(ClosureJob.status == 'completed').order_by(
        ClosureJob.completed_at.desc(), ClosureJob.id.desc(),
    ).all()
    return [
        {
            'id': lote.id,
            'mes_servico': lote.reference_month,
            'criado_em': lote.legacy_created_at or lote.created_at,
            'total_titulos': len(lote.result['payment_billing_ids']),
            'recuperado': lote.legacy_created_at is not None,
        }
        for lote in lotes
        if isinstance((lote.result or {}).get('payment_billing_ids'), list)
    ]


def listar_meses(db: Session) -> list[dict]:
    meses: dict[str, int] = {}
    for lote in db.query(ClosureJob).filter(ClosureJob.status == 'completed').all():
        if isinstance((lote.result or {}).get('payment_billing_ids'), list):
            meses[lote.reference_month] = meses.get(lote.reference_month, 0) + 1
    return [
        {'mes_servico': mes, 'total_lotes': meses[mes]}
        for mes in sorted(meses, reverse=True)
    ]


def _mes_anterior(data: date) -> str:
    ano = data.year if data.month > 1 else data.year - 1
    mes = data.month - 1 if data.month > 1 else 12
    return f'{ano:04d}-{mes:02d}'


def _referencia_legada(billings: list[Billing], efetivos: list[Billing]) -> str | None:
    """Só rotula o serviço quando o período pode ser identificado sem ambiguidade."""
    periodos = {b.period_label for b in billings if b.billing_type == CONSOLIDATED_BILLING_TYPE}
    if periodos:
        if len(periodos) != 1:
            return None
        periodo = next(iter(periodos))
        if not periodo or not re.fullmatch(r'(0[1-9]|1[0-2])/\d{4}', periodo):
            return None
        mes, ano = periodo.split('/')
        return f'{ano}-{mes}'
    vencimentos = {(b.due_date.year, b.due_date.month) for b in efetivos if b.due_date}
    if len(vencimentos) != 1:
        return None
    ano, mes = next(iter(vencimentos))
    return _mes_anterior(date(ano, mes, 1))


def recuperar_lotes_anteriores(db: Session) -> list[dict]:
    """Reconstitui execuções anteriores pelo timestamp exato da transação.

    O fechamento antigo gravava todos os títulos de uma execução com o mesmo
    ``created_at`` no PostgreSQL. Exigimos também um marcador criado somente
    pelo fechamento; carnês e cobranças avulsas do mês ficam de fora.
    """
    marcadores = db.query(Billing.created_at).filter(
        or_(
            Billing.billing_type == CONSOLIDATED_BILLING_TYPE,
            Billing.notes.ilike('Fechamento %'),
            Billing.notes.ilike('Taxas processadas no fechamento:%'),
        ),
    ).distinct().order_by(Billing.created_at.desc()).limit(300).all()
    jobs = db.query(ClosureJob).all()
    timestamps = {j.legacy_created_at for j in jobs if j.legacy_created_at is not None}
    registrados = {
        int(billing_id)
        for job in jobs
        for billing_id in (job.result or {}).get('payment_billing_ids', [])
    }
    tipos = {
        'recorrente', 'prorata', 'primeira_mensalidade',
        'taxa_desinstalacao', 'item', CONSOLIDATED_BILLING_TYPE,
    }
    for (criado_em,) in marcadores:
        if criado_em in timestamps:
            continue
        billings = db.query(Billing).filter(Billing.created_at == criado_em).order_by(Billing.id).all()
        if len(billings) > 1000:
            continue  # Importações em massa não são uma execução individual.
        efetivos = [
            b for b in billings
            if not b.is_deleted and b.substituted_by_id is None and b.billing_type in tipos
        ]
        ids = [b.id for b in efetivos]
        if not ids or any(billing_id in registrados for billing_id in ids):
            continue
        referencia = _referencia_legada(billings, efetivos)
        if referencia is None:
            continue
        try:
            with db.begin_nested():
                job = ClosureJob(
                    reference_month=referencia,
                    filter_type='legacy', status='completed',
                    legacy_created_at=criado_em,
                    started_at=criado_em, completed_at=criado_em,
                    result={'payment_billing_ids': ids},
                )
                db.add(job)
                db.flush()
        except IntegrityError:
            continue  # Outro operador já recuperou esta execução.
        timestamps.add(criado_em)
        registrados.update(ids)
    db.commit()
    return listar_lotes(db)


def conferir_lote(db: Session, lote_id: int, billing_id: int | None = None, *, ignorar_entrega=False) -> dict:
    lote, ids = _lote(db, lote_id)
    if billing_id is not None:
        if billing_id not in ids:
            raise HTTPException(status_code=404, detail='Cobrança não pertence a este fechamento.')
        ids = [billing_id]
    billings = {b.id: b for b in db.query(Billing).filter(Billing.id.in_(ids)).all()}
    boletos = {b.billing_id: b for b in db.query(AilosBoleto).filter(AilosBoleto.billing_id.in_(ids)).all()}
    notas = {n.billing_id: n for n in db.query(NfseNota).filter(NfseNota.billing_id.in_(ids)).all()}
    entregas = {e.billing_id: e for e in db.query(ClosureEmailDelivery).filter(
        ClosureEmailDelivery.closure_job_id == lote_id,
    ).all()}

    itens = []
    for billing_id in ids:
        b = billings.get(billing_id)
        payer = None
        if b:
            owner = db.get(Client, b.client_id)
            if owner:
                try:
                    payer = resolver_pagador(db, b, owner)
                except ValueError:
                    payer = None
        boleto = boletos.get(billing_id)
        nota = notas.get(billing_id)
        entrega = None if ignorar_entrega else entregas.get(billing_id)
        fiscal = payer.issue_invoice if payer else None
        emails = emails_cadastrados(payer) if payer else []
        boleto_ok = bool(boleto and boleto.linha_digitavel and boleto.codigo_barras)
        motivos = []
        if not b or b.is_deleted or b.substituted_by_id is not None or b.status not in (
            BillingStatus.PENDING, BillingStatus.OVERDUE,
        ):
            motivos.append('Cobrança não está em aberto')
        if not payer or payer.is_deleted:
            motivos.append('Responsável financeiro indisponível')
        elif not emails:
            motivos.append('Responsável financeiro sem e-mail')
        if not boleto_ok:
            motivos.append('Boleto Ailos não emitido')
        elif boleto.baixa_status is not None:
            motivos.append('Boleto baixado ou com baixa pendente')
        elif boleto.status_ailos in ('3', '5'):
            motivos.append('Boleto já baixado ou liquidado na Ailos')
        if fiscal == 'sim' and (not nota or nota.status != 'emitida'):
            motivos.append('NFS-e obrigatória ainda não emitida')
        elif fiscal not in ('sim', 'nao'):
            motivos.append('Preferência de NFS-e não informada')

        if entrega and entrega.status == 'enviado':
            estado = 'enviado'
            motivo = None
        elif entrega and entrega.status in ('aguardando', 'processando', 'desconhecido'):
            estado = entrega.status
            motivo = entrega.error or ('Aguardando envio automático' if estado == 'aguardando'
                                      else 'Confirme o resultado do envio antes de tentar novamente')
        elif motivos:
            estado = 'bloqueado'
            motivo = '; '.join(motivos)
        else:
            estado = 'pronto'
            motivo = entrega.error if entrega and entrega.status == 'erro' else None

        itens.append({
            'lote_id': lote.id,
            'billing_id': billing_id,
            'cliente': payer.name if payer else 'Responsável indisponível',
            'email': ', '.join(emails) or None,
            'emails': emails,
            'valor': float(b.amount) if b else 0,
            'vencimento': b.due_date if b else None,
            'boleto_emitido': boleto_ok,
            'nfse_status': nota.status if nota else None,
            'emitir_nfse': fiscal,
            'documentos': ['Boleto', 'NFS-e'] if fiscal == 'sim' else ['Boleto'],
            'estado': estado,
            'motivo': motivo,
            'enviado_em': entrega.sent_at if entrega else None,
            'proxima_tentativa_em': entrega.next_attempt_at if entrega else None,
        })

    return {
        'id': lote.id,
        'total_lotes': 1,
        'mes_servico': lote.reference_month,
        'criado_em': lote.created_at,
        'total': len(itens),
        'prontos': sum(item['estado'] == 'pronto' for item in itens),
        'enviados': sum(item['estado'] == 'enviado' for item in itens),
        'em_fila': sum(item['estado'] == 'aguardando' for item in itens),
        'limite_destinatarios_hora': _limite_envio(),
        'bloqueados': sum(item['estado'] == 'bloqueado' for item in itens),
        'indeterminados': sum(item['estado'] in ('processando', 'desconhecido') for item in itens),
        'itens': itens,
    }


def conferir_mes(db: Session, mes_servico: str) -> dict:
    """Reúne todos os títulos efetivos dos fechamentos do mês, sem paginar."""
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', mes_servico):
        raise HTTPException(status_code=422, detail='Mês do serviço inválido. Use YYYY-MM.')
    lotes = db.query(ClosureJob).filter(
        ClosureJob.status == 'completed', ClosureJob.reference_month == mes_servico,
    ).order_by(ClosureJob.completed_at.desc(), ClosureJob.id.desc()).all()
    itens = []
    vistos: set[int] = set()
    for lote in lotes:
        if not isinstance((lote.result or {}).get('payment_billing_ids'), list) or not lote.result['payment_billing_ids']:
            continue
        previa = conferir_lote(db, lote.id)
        for item in previa['itens']:
            if item['billing_id'] not in vistos:
                itens.append({'lote_id': lote.id, **item})
                vistos.add(item['billing_id'])
    if not itens:
        raise HTTPException(status_code=404, detail='Nenhum fechamento encontrado para este mês.')
    itens.sort(key=lambda item: (item['cliente'].casefold(), item['billing_id']))
    return {
        'mes_servico': mes_servico,
        'total_lotes': len(lotes),
        'total': len(itens),
        'prontos': sum(item['estado'] == 'pronto' for item in itens),
        'enviados': sum(item['estado'] == 'enviado' for item in itens),
        'em_fila': sum(item['estado'] == 'aguardando' for item in itens),
        'bloqueados': sum(item['estado'] == 'bloqueado' for item in itens),
        'indeterminados': sum(item['estado'] in ('processando', 'desconhecido') for item in itens),
        'itens': itens,
    }


def _limite_envio():
    from app.services.smtp_rate_limit import hourly_limit
    return hourly_limit()


def enfileirar_lote(db: Session, lote_id: int, billing_ids: list[int] | None = None) -> dict:
    """Uma transação agenda toda a seleção, sem transmitir no request HTTP."""
    from app.services.email_smtp import load_config
    previa = conferir_lote(db, lote_id)
    selected = set(billing_ids) if billing_ids is not None else {i['billing_id'] for i in previa['itens']}
    if not selected.issubset({i['billing_id'] for i in previa['itens']}):
        raise HTTPException(status_code=404, detail='Cobrança não pertence a este fechamento.')
    config = load_config(db)
    if not config['host'] or not config['from_email']:
        raise HTTPException(status_code=400, detail='SMTP não configurado para envio por e-mail.')
    queued, ignored = [], []
    for item in previa['itens']:
        billing_id = item['billing_id']
        if billing_id not in selected:
            continue
        if item['estado'] != 'pronto':
            ignored.append({'billing_id': billing_id, 'estado': item['estado'], 'motivo': item['motivo']})
            continue
        try:
            with db.begin_nested():
                entrega = db.query(ClosureEmailDelivery).filter_by(
                    closure_job_id=lote_id, billing_id=billing_id,
                ).with_for_update().first()
                if entrega and entrega.status != 'erro':
                    ignored.append({'billing_id': billing_id, 'estado': entrega.status, 'motivo': 'Envio já reservado.'})
                    continue
                if not entrega:
                    entrega = ClosureEmailDelivery(closure_job_id=lote_id, billing_id=billing_id)
                    db.add(entrega)
                entrega.status = 'aguardando'
                entrega.error = None
                entrega.next_attempt_at = None
                entrega.recipient = item['email']
                db.flush()
                queued.append(billing_id)
        except IntegrityError:
            ignored.append({'billing_id': billing_id, 'estado': 'aguardando', 'motivo': 'Envio já reservado.'})
    db.commit()
    return {'enfileirados': queued, 'ignorados': ignored, 'limite_destinatarios_hora': _limite_envio()}


def processar_fila(db: Session) -> None:
    """Processa no máximo um título; o scheduler chama de novo sem dormir aqui."""
    from app.services.smtp_rate_limit import utc, utcnow
    now = utcnow()
    # Após queda no meio da transmissão, não presumir que o SMTP recusou.
    db.query(ClosureEmailDelivery).filter(
        ClosureEmailDelivery.status == 'processando',
        ClosureEmailDelivery.started_at < now - timedelta(minutes=15),
    ).update({'status': 'desconhecido', 'error': 'Envio interrompido; confira o servidor de e-mail antes de reenviar.'}, synchronize_session=False)
    db.commit()
    entrega = db.query(ClosureEmailDelivery).filter_by(status='aguardando').order_by(ClosureEmailDelivery.id).first()
    if not entrega or (entrega.next_attempt_at and utc(entrega.next_attempt_at) > now):
        return
    # Compare-and-set: dois workers não podem transmitir o mesmo título.
    claimed = db.query(ClosureEmailDelivery).filter_by(id=entrega.id, status='aguardando').update(
        {'status': 'processando', 'started_at': now}, synchronize_session=False,
    )
    db.commit()
    if not claimed:
        return
    db.refresh(entrega)
    transmitir_titulo(db, entrega)


def transmitir_titulo(db: Session, entrega: ClosureEmailDelivery) -> None:
    from app.api.v1.endpoints.boletos import preparar_envio_boleto_email
    from app.services.email_smtp import (
        EmailConfigError, EmailRateLimitError, enviar_email, load_config, verificar_intervalo,
    )
    transmitting = False
    try:
        item = conferir_lote(db, entrega.closure_job_id, entrega.billing_id, ignorar_entrega=True)['itens'][0]
        if item['estado'] != 'pronto':
            raise EmailConfigError(item['motivo'] or 'Cobrança não está pronta para envio.')
        config = load_config(db)
        if not config['host'] or not config['from_email']:
            raise EmailConfigError('SMTP não configurado para envio por e-mail.')
        # Não baixa PDFs enquanto aguarda o intervalo. O SMTP reserva a quota
        # novamente, de forma atômica, imediatamente antes da transmissão.
        verificar_intervalo(db, config, item['email'])
        destinatario, assunto, corpo, anexos = preparar_envio_boleto_email(
            db, entrega.billing_id, item['emitir_nfse'] == 'sim',
        )
        entrega.recipient = destinatario
        db.commit()
        transmitting = True
        enviar_email(db, destinatario=destinatario, assunto=assunto, corpo=corpo,
                     anexos=anexos, config=config)
    except EmailRateLimitError as exc:
        entrega.status = 'aguardando'
        entrega.next_attempt_at = exc.retry_at
        entrega.error = 'Aguardando o intervalo de segurança da conta de e-mail. O envio será retomado automaticamente.'
    except EmailConfigError as exc:
        entrega.status = 'erro'
        entrega.error = str(exc)
    except Exception as exc:
        entrega.status = 'desconhecido' if transmitting else 'erro'
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        entrega.error = (f'Resultado do envio incerto: {detail}' if transmitting
                         else f'Falha ao preparar os documentos: {detail}')
    else:
        entrega.status = 'enviado'
        entrega.error = None
        entrega.next_attempt_at = None
        entrega.sent_at = datetime.now(timezone.utc)
    db.commit()
