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

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.enums import BillingStatus
from app.models.client import Client
from app.models.nfse_lote import NfseLote
from app.models.nfse_nota import NfseNota
# Interveniente financeiro = quem responde pela cobrança. Quando existe, ele é o
# tomador da NFS-e (coerente com o pagador do boleto). resolver_pagador devolve
# o interveniente-ou-cliente da cobrança.
from app.services.ailos_boletos import resolver_pagador

logger = logging.getLogger(__name__)

# Uma cobrança já "resolvida" (não deve reentrar num lote) tem nota nestes
# estados. 'erro' fica de fora de propósito: falha pode ser reprocessada.
_STATUS_BLOQUEIA_REEMISSAO = {'emitida', 'pending', 'processing'}


class LoteError(Exception):
    """Erro de validação na montagem do lote (nada é gravado)."""


# ---------------------------------------------------------------------------
# Elegibilidade
# ---------------------------------------------------------------------------

def listar_elegiveis(
    db: Session,
    period_label: str,
    *,
    busca: str | None = None,
    tipo: str | None = None,
) -> dict:
    """
    Candidatos à emissão no lote de fechamento informado: cobranças cujo cliente
    tem ``issue_invoice == 'sim'`` e que ainda não possuem NFS-e emitida/em voo.

    ``busca`` filtra por nome/razão social ou CPF/CNPJ; ``tipo`` por pf/pj.
    """
    # Filtros de tomador (issue_invoice/busca/tipo) são aplicados em Python sobre
    # o tomador RESOLVIDO (interveniente-ou-cliente), não sobre o dono do veículo.
    query = (
        db.query(Billing, Client, NfseNota, AilosBoleto)
        .join(Client, Client.id == Billing.client_id)
        .outerjoin(NfseNota, NfseNota.billing_id == Billing.id)
        .outerjoin(AilosBoleto, AilosBoleto.billing_id == Billing.id)
        .filter(
            Billing.is_deleted.is_(False),
            # Cobrança cancelada (inclui as originais consolidadas em boleto
            # único) não é elegível para NFS-e.
            Billing.status != BillingStatus.CANCELED,
            Billing.period_label == period_label,
            Client.is_deleted.is_(False),
        )
    )

    busca_alvo = (busca or '').strip().lower()
    itens: list[dict] = []
    ja_emitidas = 0
    for billing, owner, nota, boleto in query.all():
        tomador = resolver_pagador(db, billing, owner)
        # Só emite NF para tomador com issue_invoice == 'sim'.
        if (tomador.issue_invoice or '') != 'sim':
            continue
        if tipo in ('pf', 'pj') and tomador.type != tipo:
            continue
        if busca_alvo and busca_alvo not in (tomador.name or '').lower() \
                and busca_alvo not in (tomador.cpf_cnpj or '').lower():
            continue
        if nota is not None and nota.status in _STATUS_BLOQUEIA_REEMISSAO:
            if nota.status == 'emitida':
                ja_emitidas += 1
            continue
        itens.append({
            'billing_id': billing.id,
            'client_id': tomador.id,
            'tomador': tomador.name,
            'cpf_cnpj': tomador.cpf_cnpj,
            'tipo': tomador.type,
            'cidade': tomador.city,
            'nosso_numero': boleto.nosso_numero if boleto else None,
            'valor': float(billing.amount) if billing.amount is not None else 0.0,
            'titulo': billing.title,
            # Cobrança com nota em 'erro' → reprocessamento
            'reprocessamento': nota is not None and nota.status == 'erro',
        })

    itens.sort(key=lambda i: (i['tomador'] or '').lower())

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
                try:
                    # `db.add` acontece DENTRO do savepoint — ver comentário
                    # equivalente em `ailos_boletos._upsert_ailos_boleto` sobre
                    # por que precisa ser assim (`begin_nested()` flusha
                    # pendências antes de abrir o SAVEPOINT).
                    with db.begin_nested():
                        nota = NfseNota(billing_id=billing_id)
                        db.add(nota)
                        db.flush()
                except IntegrityError:
                    # Corrida: outro lote/emissão avulsa concorrente já criou a
                    # nota deste billing_id entre o SELECT e este INSERT
                    # (billing_id é UNIQUE — ver app/models/nfse_nota.py). O
                    # SAVEPOINT isola a falha: só esta iteração é descartada, o
                    # resto do lote (já commitado ou ainda por vir) segue intacto.
                    nota = db.query(NfseNota).filter_by(billing_id=billing_id).first()
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
        lote = db.get(NfseLote, lote_id)
        codigo = lote.codigo_servico if lote else None
        notas = db.query(NfseNota).filter(
            NfseNota.lote_id == lote_id, NfseNota.status == 'pending'
        ).all()
        for nota in notas:
            _emitir_uma(db, nota, nfse_provider.emitir_nfse, codigo)
        _fechar_lote(db, lote_id)
    except Exception:  # pragma: no cover — rede de segurança da thread
        logger.exception('Falha inesperada ao processar lote NFS-e %s', lote_id)
        db.rollback()
    finally:
        db.close()


def _emitir_uma(db: Session, nota: NfseNota, emitir_fn, cod_trib_nacional=None) -> None:
    billing = db.get(Billing, nota.billing_id)
    # Tomador = interveniente do contrato, quando houver; senão o cliente da cobrança.
    owner = db.get(Client, billing.client_id) if billing else None
    client = resolver_pagador(db, billing, owner) if (billing and owner) else None
    if billing is None or client is None:
        nota.status = 'erro'
        nota.erro_mensagem = 'Cobrança ou cliente não encontrado.'
        db.commit()
        return
    from app.services import nfse_provider  # tardio: evita ciclo de import (ver _processar_lote)
    erros_esperados = (*nfse_provider.ErrosConfig, *nfse_provider.ErrosApi)

    try:
        emitir_fn(db, billing, client, cod_trib_nacional=cod_trib_nacional)
    except erros_esperados as exc:
        # emitir_nfse já pode ter marcado 'erro' e commitado; garante a mensagem.
        db.rollback()
        nota = db.get(NfseNota, nota.id)
        nota.status = 'erro'
        nota.erro_mensagem = (nota.erro_mensagem or str(exc))[:2000]
        db.commit()
    except Exception as exc:  # noqa: BLE001 — bug inesperado, não falha de negócio/API
        # Mesmo desfecho pro usuário (nota marcada 'erro', lote segue para as
        # próximas), mas logado como exceção pra distinguir de erro de negócio.
        logger.exception('Falha inesperada ao emitir NFS-e da nota %s', nota.id)
        db.rollback()
        nota = db.get(NfseNota, nota.id)
        nota.status = 'erro'
        nota.erro_mensagem = (nota.erro_mensagem or str(exc))[:2000]
        db.commit()


def recuperar_notas_orfas(db: Session) -> int:
    """Recupera notas presas em 'pending'/'processing' de uma execução anterior.

    A emissão roda em thread daemon; um reinício do processo (deploy, crash, OOM)
    mata a thread em voo e deixa notas 'pending'/'processing' que NUNCA mais são
    tocadas — e ``listar_elegiveis`` as trata como "em voo", então não reaparecem
    para reprocessar. No boot ainda NÃO há worker ativo, logo qualquer nota nesses
    estados é órfã: vira 'erro' (reprocessável) e o lote é fechado. Chamado no
    startup. Retorna quantas notas foram recuperadas.
    """
    orfas = db.query(NfseNota).filter(NfseNota.status.in_(('pending', 'processing'))).all()
    if not orfas:
        return 0
    lote_ids = set()
    for nota in orfas:
        nota.status = 'erro'
        nota.erro_mensagem = 'Emissão interrompida por reinício do servidor — reprocesse o lote.'
        if nota.lote_id:
            lote_ids.add(nota.lote_id)
    db.commit()
    for lote_id in lote_ids:
        _fechar_lote(db, lote_id)
    return len(orfas)


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
    itens = []
    for nota, billing, client in rows:
        tomador = resolver_pagador(db, billing, client)
        itens.append({
            'nota_id': nota.id,
            'billing_id': nota.billing_id,
            'tomador': tomador.name,
            'cpf_cnpj': tomador.cpf_cnpj,
            'valor': float(billing.amount) if billing.amount is not None else 0.0,
            'numero_nfse': nota.numero_nfse,
            'status': nota.status,
            'chave_acesso': nota.chave_acesso,
            'link_visualizacao': nota.link_visualizacao,
            'erro_codigo': nota.erro_codigo,
            'erro_mensagem': nota.erro_mensagem,
        })
    return {**_lote_resumo(lote), 'itens': itens}


# ---------------------------------------------------------------------------
# Listagem geral de notas + balanço (painel)
# ---------------------------------------------------------------------------

def _intervalo_do_mes(competencia: str | None) -> tuple[datetime, datetime]:
    """'YYYY-MM' → (início, fim exclusivo) em UTC. Vazio = mês corrente."""
    hoje = datetime.now(timezone.utc)
    ano, mes = hoje.year, hoje.month
    if competencia:
        try:
            ano, mes = (int(p) for p in competencia.split('-')[:2])
        except (ValueError, TypeError):
            pass
    inicio = datetime(ano, mes, 1, tzinfo=timezone.utc)
    fim = datetime(ano + (mes == 12), (mes % 12) + 1, 1, tzinfo=timezone.utc)
    return inicio, fim


def listar_notas(
    db: Session,
    *,
    busca: str | None = None,
    situacao: str | None = None,
    period_label: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """Listagem paginada de TODAS as NFS-e (tela "Notas"), com contexto da
    cobrança, do tomador e do boleto (nosso número)."""
    query = (
        db.query(NfseNota, Billing, Client, AilosBoleto)
        .join(Billing, Billing.id == NfseNota.billing_id)
        .join(Client, Client.id == Billing.client_id)
        .outerjoin(AilosBoleto, AilosBoleto.billing_id == Billing.id)
        .filter(Billing.is_deleted.is_(False))
    )
    if (busca or '').strip():
        alvo = f'%{busca.strip()}%'
        query = query.filter(or_(
            Client.name.ilike(alvo),
            Client.cpf_cnpj.ilike(alvo),
            NfseNota.numero_nfse.ilike(alvo),
        ))
    if situacao:
        query = query.filter(NfseNota.status == situacao)
    if period_label:
        query = query.filter(Billing.period_label == period_label)

    total = query.count()
    rows = query.order_by(NfseNota.id.desc()).offset(max(offset, 0)).limit(limit).all()

    itens = []
    for nota, billing, client, boleto in rows:
        tomador = resolver_pagador(db, billing, client)
        itens.append({
            'nota_id': nota.id,
            'billing_id': nota.billing_id,
            'lote_id': nota.lote_id,
            'tomador': tomador.name,
            'cpf_cnpj': tomador.cpf_cnpj,
            'valor': float(billing.amount) if billing.amount is not None else 0.0,
            'nosso_numero': boleto.nosso_numero if boleto else None,
            'numero_nfse': nota.numero_nfse,
            'status': nota.status,
            'chave_acesso': nota.chave_acesso,
            'link_visualizacao': nota.link_visualizacao,
            'erro_codigo': nota.erro_codigo,
            'erro_mensagem': nota.erro_mensagem,
            'tem_xml': bool(nota.xml_retorno),
            'data_ocorrencia': (nota.data_emissao or getattr(nota, 'created_at', None) or None)
                               and (nota.data_emissao or nota.created_at).isoformat(),
        })

    return {'total': total, 'limit': limit, 'offset': offset, 'itens': itens}


def resumo(db: Session, competencia: str | None = None) -> dict:
    """Balanço do mês para o painel: autorizadas × negadas (+ em processamento)."""
    inicio, fim = _intervalo_do_mes(competencia)
    base = db.query(NfseNota).filter(
        NfseNota.created_at >= inicio, NfseNota.created_at < fim
    )
    autorizadas = base.filter(NfseNota.status == 'emitida').count()
    negadas = base.filter(NfseNota.status == 'erro').count()
    processando = base.filter(NfseNota.status.in_(('pending', 'processing'))).count()
    return {
        'competencia': inicio.strftime('%m/%Y'),
        'autorizadas': autorizadas,
        'negadas': negadas,
        'processando': processando,
        'total': autorizadas + negadas + processando,
        'total_geral': db.query(NfseNota).count(),
    }


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
