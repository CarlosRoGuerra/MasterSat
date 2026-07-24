"""
Emissão de NFS-e em LOTE a partir de um fechamento financeiro.

Fluxo (espelha a especificação do cliente / vídeo do SGR):
  1. O operador informa o lote de fechamento (``period_label``, ex.: '07/2026').
  2. ``listar_elegiveis`` lista as cobranças desse lote cujo cliente tem
     "Emitir NF = Sim", já aplicando idempotência (não relista o que já foi
     emitido; relista o que falhou para reprocessar).
  3. O operador confere, desmarca exceções e confirma.
  4. ``criar_lote`` cria o ``NfseLote`` + um ``NfseNota`` pendente por cobrança,
     TRANSACIONALMENTE (falha → nada é gravado), e dispara a emissão assíncrona.
  5. Uma thread emite cada nota via ``nfse_nacional.emitir_nfse`` e atualiza os
     contadores; a UI acompanha por ``consultar_lote``.

O disparo real depende do certificado ICP-Brasil (bloqueio conhecido). Sem ele,
cada nota do lote termina em ``erro`` com a mensagem clara do módulo de emissão —
o lote em si funciona e é testável.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.billing import Billing
from app.models.client import Client
from app.models.nfse_lote import NfseLote
from app.models.nfse_nota import NfseNota

logger = logging.getLogger(__name__)

# Uma cobrança já "resolvida" (não deve reentrar num lote) tem nota nestes
# estados. 'erro' fica de fora de propósito: falha pode ser reprocessada.
_STATUS_BLOQUEIA_REEMISSAO = {'emitida', 'pending', 'processing'}


class LoteError(Exception):
    """Erro de validação na montagem do lote (nada é gravado)."""


# ---------------------------------------------------------------------------
# Elegibilidade
# ---------------------------------------------------------------------------

def listar_elegiveis(db: Session, period_label: str) -> dict:
    """
    Candidatos à emissão no lote de fechamento informado: cobranças cujo cliente
    tem ``issue_invoice == 'sim'`` e que ainda não possuem NFS-e emitida/em voo.
    """
    rows = (
        db.query(Billing, Client, NfseNota)
        .join(Client, Client.id == Billing.client_id)
        .outerjoin(NfseNota, NfseNota.billing_id == Billing.id)
        .filter(
            Billing.is_deleted.is_(False),
            Billing.period_label == period_label,
            Client.is_deleted.is_(False),
            Client.issue_invoice == 'sim',
        )
        .order_by(Client.name)
        .all()
    )

    itens: list[dict] = []
    ja_emitidas = 0
    for billing, client, nota in rows:
        if nota is not None and nota.status in _STATUS_BLOQUEIA_REEMISSAO:
            if nota.status == 'emitida':
                ja_emitidas += 1
            continue
        itens.append({
            'billing_id': billing.id,
            'client_id': client.id,
            'tomador': client.name,
            'cpf_cnpj': client.cpf_cnpj,
            'tipo': client.type,
            'valor': float(billing.amount) if billing.amount is not None else 0.0,
            'titulo': billing.title,
            # Cobrança com nota em 'erro' → reprocessamento
            'reprocessamento': nota is not None and nota.status == 'erro',
        })

    return {
        'period_label': period_label,
        'total_elegiveis': len(itens),
        'ja_emitidas': ja_emitidas,
        'itens': itens,
    }


# ---------------------------------------------------------------------------
# Criação do lote (transacional) + disparo assíncrono
# ---------------------------------------------------------------------------

def criar_lote(
    db: Session,
    period_label: str,
    billing_ids: list[int],
    *,
    competencia: date | None = None,
    codigo_servico: str | None = None,
    discriminacao: str | None = None,
    criado_por: int | None = None,
    emitir_async: bool = True,
) -> NfseLote:
    """
    Cria o lote e as notas pendentes numa única transação e dispara a emissão.

    Só entram cobranças que ainda estão elegíveis no momento da confirmação
    (revalida a idempotência para evitar corrida com outra emissão).
    """
    if not billing_ids:
        raise LoteError('Nenhuma cobrança selecionada para emissão.')

    elegiveis = {i['billing_id'] for i in listar_elegiveis(db, period_label)['itens']}
    alvo = [bid for bid in dict.fromkeys(billing_ids) if bid in elegiveis]
    if not alvo:
        raise LoteError(
            'Nenhum registro encontrado para emissão. As cobranças selecionadas '
            'já foram emitidas ou não estão mais elegíveis (lote já processado).'
        )

    try:
        lote = NfseLote(
            period_label=period_label,
            competencia=competencia,
            codigo_servico=codigo_servico,
            discriminacao=discriminacao,
            status='processando',
            total_notas=len(alvo),
            criado_por=criado_por,
        )
        db.add(lote)
        db.flush()  # garante lote.id

        for billing_id in alvo:
            nota = db.query(NfseNota).filter_by(billing_id=billing_id).first()
            if nota is None:
                nota = NfseNota(billing_id=billing_id)
                db.add(nota)
            nota.lote_id = lote.id
            nota.status = 'pending'
            nota.erro_codigo = None
            nota.erro_mensagem = None

        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(lote)
    if emitir_async:
        threading.Thread(
            target=_emitir_lote_worker, args=(lote.id,), daemon=True
        ).start()
    return lote


def _emitir_lote_worker(lote_id: int) -> None:
    """Thread: emite cada nota do lote numa sessão própria, com commit por nota."""
    from app.services import nfse_provider  # tardio: evita ciclo de import

    db = SessionLocal()
    try:
        notas = db.query(NfseNota).filter(
            NfseNota.lote_id == lote_id, NfseNota.status == 'pending'
        ).all()
        for nota in notas:
            _emitir_uma(db, nota, nfse_provider.emitir_nfse)
        _fechar_lote(db, lote_id)
    except Exception:  # pragma: no cover — rede de segurança da thread
        logger.exception('Falha inesperada ao processar lote NFS-e %s', lote_id)
        db.rollback()
    finally:
        db.close()


def _emitir_uma(db: Session, nota: NfseNota, emitir_fn) -> None:
    billing = db.get(Billing, nota.billing_id)
    client = db.get(Client, billing.client_id) if billing else None
    if billing is None or client is None:
        nota.status = 'erro'
        nota.erro_mensagem = 'Cobrança ou cliente não encontrado.'
        db.commit()
        return
    try:
        emitir_fn(db, billing, client)
    except Exception as exc:  # NfseError/NfseApiError e afins
        # emitir_nfse já pode ter marcado 'erro' e commitado; garante a mensagem.
        db.rollback()
        nota = db.get(NfseNota, nota.id)
        nota.status = 'erro'
        nota.erro_mensagem = (nota.erro_mensagem or str(exc))[:2000]
        db.commit()


def _fechar_lote(db: Session, lote_id: int) -> None:
    lote = db.get(NfseLote, lote_id)
    if lote is None:
        return
    notas = db.query(NfseNota).filter(NfseNota.lote_id == lote_id).all()
    lote.total_autorizadas = sum(1 for n in notas if n.status == 'emitida')
    lote.total_erro = sum(1 for n in notas if n.status == 'erro')
    lote.status = 'com_erro' if lote.total_erro else 'concluido'
    lote.concluido_em = datetime.now(timezone.utc)
    db.commit()


# ---------------------------------------------------------------------------
# Consulta / drill-down
# ---------------------------------------------------------------------------

def listar_lotes(db: Session, limit: int = 100) -> list[dict]:
    lotes = db.query(NfseLote).order_by(NfseLote.id.desc()).limit(limit).all()
    return [_lote_resumo(l) for l in lotes]


def consultar_lote(db: Session, lote_id: int) -> dict | None:
    lote = db.get(NfseLote, lote_id)
    if lote is None:
        return None
    rows = (
        db.query(NfseNota, Billing, Client)
        .join(Billing, Billing.id == NfseNota.billing_id)
        .join(Client, Client.id == Billing.client_id)
        .filter(NfseNota.lote_id == lote_id)
        .order_by(Client.name)
        .all()
    )
    itens = [{
        'nota_id': nota.id,
        'billing_id': nota.billing_id,
        'tomador': client.name,
        'cpf_cnpj': client.cpf_cnpj,
        'valor': float(billing.amount) if billing.amount is not None else 0.0,
        'numero_nfse': nota.numero_nfse,
        'status': nota.status,
        'chave_acesso': nota.chave_acesso,
        'link_visualizacao': nota.link_visualizacao,
        'erro_codigo': nota.erro_codigo,
        'erro_mensagem': nota.erro_mensagem,
    } for nota, billing, client in rows]
    return {**_lote_resumo(lote), 'itens': itens}


def _lote_resumo(lote: NfseLote) -> dict:
    return {
        'id': lote.id,
        'period_label': lote.period_label,
        'competencia': lote.competencia.isoformat() if lote.competencia else None,
        'codigo_servico': lote.codigo_servico,
        'discriminacao': lote.discriminacao,
        'status': lote.status,
        'total_notas': lote.total_notas,
        'total_autorizadas': lote.total_autorizadas,
        'total_erro': lote.total_erro,
        'criado_em': lote.created_at.isoformat() if getattr(lote, 'created_at', None) else None,
        'concluido_em': lote.concluido_em.isoformat() if lote.concluido_em else None,
    }
