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
from datetime import date, datetime, timedelta, timezone

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
_STATUS_BLOQUEIA_REEMISSAO = {'emitida', 'pending', 'processing', 'desconhecido'}
_ERROS_REPROCESSAVEIS = ('local', 'rejeicao')
_LEASE_FILA_SEGUNDOS = 120


def _reprocessavel(nota: NfseNota) -> bool:
    return nota.status == 'erro' and nota.erro_tipo in _ERROS_REPROCESSAVEIS


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
    billing_ids: list[int] | None = None,
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
            Client.is_deleted.is_(False),
        )
    )
    # A seleção manual pode abranger cobranças de meses diferentes e avulsas
    # sem period_label. A consulta do fechamento continua restrita ao mês.
    query = query.filter(
        Billing.period_label == period_label if billing_ids is None
        else Billing.id.in_(billing_ids)
    )

    busca_alvo = (busca or '').strip().lower()
    itens: list[dict] = []
    ja_emitidas = 0
    for billing, owner, nota, boleto in query.populate_existing().all():
        tomador = resolver_pagador(db, billing, owner)
        # Só emite NF para tomador com issue_invoice == 'sim'.
        if (tomador.issue_invoice or '') != 'sim':
            continue
        if tipo in ('pf', 'pj') and tomador.type != tipo:
            continue
        if busca_alvo and busca_alvo not in (tomador.name or '').lower() \
                and busca_alvo not in (tomador.cpf_cnpj or '').lower():
            continue
        if nota is not None and not _reprocessavel(nota):
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


def previsualizar_selecionados(db: Session, billing_ids: list[int]) -> dict:
    """Mostra quem não permite NFS-e antes da confirmação, sem emitir nada."""
    alvos = sorted(set(billing_ids))
    elegiveis = {
        item['billing_id'] for item in listar_elegiveis(
            db, 'Seleção manual', billing_ids=alvos,
        )['itens']
    }
    nao_emitem = []
    outros_ignorados = []
    encontrados = set()
    for billing in db.query(Billing).filter(Billing.id.in_(alvos)).all():
        encontrados.add(billing.id)
        if billing.id in elegiveis:
            continue
        owner = db.get(Client, billing.client_id)
        tomador = resolver_pagador(db, billing, owner) if owner else None
        if billing.is_deleted or billing.status == BillingStatus.CANCELED or not tomador or tomador.is_deleted:
            outros_ignorados.append(billing.id)
        elif tomador.issue_invoice != 'sim':
            nao_emitem.append({'billing_id': billing.id, 'cliente': tomador.name})
        else:
            outros_ignorados.append(billing.id)  # nota já emitida/em processamento
    outros_ignorados.extend(set(alvos) - encontrados)
    return {
        'elegiveis': sorted(elegiveis),
        'nao_emitem': sorted(nao_emitem, key=lambda item: (item['cliente'], item['billing_id'])),
        'outros_ignorados': sorted(outros_ignorados),
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
    selecionados_livres: bool = False,
) -> NfseLote:
    """
    Cria o lote e as notas pendentes numa única transação e dispara a emissão.

    Só entram cobranças que ainda estão elegíveis no momento da confirmação
    (revalida a idempotência para evitar corrida com outra emissão).
    """
    if not billing_ids:
        raise LoteError('Nenhuma cobrança selecionada para emissão.')

    try:
        # A mesma ordem de locks em lotes concorrentes evita deadlocks. A
        # elegibilidade é lida DEPOIS do lock; o mapa da tela não é uma reserva.
        alvos = sorted(set(billing_ids))
        db.query(Billing).filter(Billing.id.in_(alvos)).order_by(Billing.id).with_for_update().all()
        candidatos = (
            listar_elegiveis(db, period_label, billing_ids=alvos)
            if selecionados_livres else listar_elegiveis(db, period_label)
        )
        elegiveis = {i['billing_id'] for i in candidatos['itens']}
        lote = NfseLote(
            period_label=period_label,
            competencia=competencia,
            codigo_servico=codigo_servico,
            discriminacao=discriminacao,
            status='processando',
            total_notas=0,
            criado_por=criado_por,
        )
        db.add(lote)
        db.flush()  # garante lote.id

        for billing_id in alvos:
            if billing_id not in elegiveis:
                continue
            nota = db.query(NfseNota).filter_by(billing_id=billing_id).populate_existing().with_for_update().first()
            valores = dict(
                lote_id=lote.id, status='pending', tentativa_id=None,
                emissor_id=f'lote:{lote.id}', envio_iniciado_em=None,
                lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=_LEASE_FILA_SEGUNDOS),
                heartbeat_at=datetime.now(timezone.utc),
                competencia=competencia, discriminacao=discriminacao,
                codigo_servico=codigo_servico,
            )
            if nota is None:
                try:
                    # `db.add` acontece DENTRO do savepoint — ver comentário
                    # equivalente em `ailos_boletos._upsert_ailos_boleto` sobre
                    # por que precisa ser assim (`begin_nested()` flusha
                    # pendências antes de abrir o SAVEPOINT).
                    with db.begin_nested():
                        nota = NfseNota(billing_id=billing_id, **valores)
                        db.add(nota)
                        db.flush()
                except IntegrityError:
                    # Corrida: outro lote/emissão avulsa concorrente já criou a
                    # nota deste billing_id entre o SELECT e este INSERT
                    # (billing_id é UNIQUE — ver app/models/nfse_nota.py). O
                    # SAVEPOINT isola a falha: só esta iteração é descartada, o
                    # resto do lote (já commitado ou ainda por vir) segue intacto.
                    # O vencedor é dono da nota, mesmo que seu objeto ainda
                    # pareça elegível nesta sessão. Nunca o anexar a este lote.
                    db.query(NfseNota).filter_by(billing_id=billing_id).populate_existing().first()
                    continue
            elif _reprocessavel(nota):
                from app.services.nfse_emissao import arquivar_tentativa
                valores['tentativas_anteriores'] = arquivar_tentativa(nota)
                # CAS também protege concorrentes que não usam o lock Billing.
                alteradas = db.query(NfseNota).filter(
                    NfseNota.id == nota.id, NfseNota.status == 'erro',
                    NfseNota.erro_tipo.in_(_ERROS_REPROCESSAVEIS),
                    NfseNota.tentativa_id == nota.tentativa_id,
                ).update(valores, synchronize_session=False)
                if not alteradas:
                    continue
            else:
                continue
            lote.total_notas += 1

        if not lote.total_notas:
            raise LoteError(
                'Nenhum registro encontrado para emissão. As cobranças selecionadas '
                'já foram emitidas ou não estão mais elegíveis (lote já processado).'
            )

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
    parar = threading.Event()
    heartbeat = threading.Thread(target=_heartbeat_fila, args=(lote_id, parar), daemon=True)
    heartbeat.start()
    try:
        lote = db.get(NfseLote, lote_id)
        if lote is None:
            return
        codigo = lote.codigo_servico
        competencia, discriminacao = lote.competencia, lote.discriminacao
        notas = db.query(NfseNota).filter(
            NfseNota.lote_id == lote_id, NfseNota.status == 'pending'
        ).all()
        for nota in notas:
            _emitir_uma(db, nota, nfse_provider.emitir_nfse, codigo,
                       competencia=competencia, discriminacao=discriminacao)
        _fechar_lote(db, lote_id)
    except Exception:  # pragma: no cover — rede de segurança da thread
        logger.exception('Falha inesperada ao processar lote NFS-e %s', lote_id)
        db.rollback()
    finally:
        parar.set()
        heartbeat.join(timeout=2)
        db.close()


def _renovar_fila(db: Session, lote_id: int) -> int:
    agora = datetime.now(timezone.utc)
    alteradas = db.query(NfseNota).filter(
        NfseNota.lote_id == lote_id, NfseNota.status == 'pending',
        NfseNota.tentativa_id.is_(None), NfseNota.emissor_id == f'lote:{lote_id}',
        NfseNota.lease_expires_at > agora,
    ).update({
        NfseNota.heartbeat_at: agora,
        NfseNota.lease_expires_at: agora + timedelta(seconds=_LEASE_FILA_SEGUNDOS),
    }, synchronize_session=False)
    db.commit()
    return alteradas


def _heartbeat_fila(lote_id: int, parar: threading.Event) -> None:
    while not parar.wait(30):
        db = SessionLocal()
        try:
            _renovar_fila(db, lote_id)
        except Exception:
            db.rollback()
            logger.exception('Falha ao renovar agendamento do lote NFS-e %s', lote_id)
        finally:
            db.close()


def _erro_antes_da_reserva(db: Session, nota_id: int, lote_id: int | None, mensagem: str) -> None:
    # O provider trata o resultado da tentativa com token. A proteção da fila
    # só pode tratar falha anterior à reserva; nunca altera resultado fiscal.
    db.rollback()
    db.query(NfseNota).filter(
        NfseNota.id == nota_id, NfseNota.lote_id == lote_id,
        NfseNota.status == 'pending', NfseNota.tentativa_id.is_(None),
        NfseNota.emissor_id == f'lote:{lote_id}',
    ).update({
        NfseNota.status: 'erro', NfseNota.erro_tipo: 'local',
        NfseNota.erro_mensagem: mensagem[:2000],
        NfseNota.lease_expires_at: None, NfseNota.emissor_id: None,
    }, synchronize_session=False)
    db.commit()


def _emitir_uma(db: Session, nota: NfseNota, emitir_fn, cod_trib_nacional=None,
               *, competencia: date | None = None, discriminacao: str | None = None) -> None:
    db.refresh(nota)
    if nota.status != 'pending':
        return
    nota_id, lote_id = nota.id, nota.lote_id
    billing = db.get(Billing, nota.billing_id)
    # Tomador = interveniente do contrato, quando houver; senão o cliente da cobrança.
    owner = db.get(Client, billing.client_id) if billing else None
    client = resolver_pagador(db, billing, owner) if (billing and owner) else None
    if billing is None or client is None:
        _erro_antes_da_reserva(db, nota_id, lote_id, 'Cobrança ou cliente não encontrado.')
        return
    if billing.is_deleted or billing.status == BillingStatus.CANCELED or client.is_deleted:
        _erro_antes_da_reserva(db, nota_id, lote_id, 'Cobrança cancelada/excluída ou tomador excluído.')
        return
    if client.issue_invoice != 'sim':
        _erro_antes_da_reserva(db, nota_id, lote_id, 'Tomador não está configurado para emitir nota fiscal.')
        return
    from app.services import nfse_provider  # tardio: evita ciclo de import (ver _processar_lote)
    erros_esperados = (*nfse_provider.ErrosConfig, *nfse_provider.ErrosApi)

    try:
        emitir_fn(db, billing, client, cod_trib_nacional=cod_trib_nacional,
                  competencia=competencia, discriminacao=discriminacao, lote_id=lote_id)
    except erros_esperados as exc:
        _erro_antes_da_reserva(db, nota_id, lote_id, str(exc))
    except Exception as exc:  # noqa: BLE001 — bug inesperado, não falha de negócio/API
        # Mesmo desfecho pro usuário (nota marcada 'erro', lote segue para as
        # próximas), mas logado como exceção pra distinguir de erro de negócio.
        logger.exception('Falha inesperada ao emitir NFS-e da nota %s', nota.id)
        _erro_antes_da_reserva(db, nota_id, lote_id, str(exc))


def recuperar_notas_orfas(db: Session) -> int:
    """Marca apenas leases expiradas para consulta, preservando outros workers.

    A migration já converte pendentes legados sem lease em desconhecido. Nenhuma
    recuperação autoriza reenvio, nem consulta a rede durante o boot.
    """
    from app.services.nfse_emissao import recuperar_expiradas

    ids = recuperar_expiradas(db)
    if not ids:
        return 0
    lote_ids = {row[0] for row in db.query(NfseNota.lote_id).filter(
        NfseNota.id.in_(ids), NfseNota.lote_id.is_not(None),
    ).all()}
    for lote_id in lote_ids:
        _fechar_lote(db, lote_id)
    return len(ids)


def _fechar_lote(db: Session, lote_id: int) -> None:
    lote = db.get(NfseLote, lote_id)
    if lote is None:
        return
    notas = db.query(NfseNota).filter(NfseNota.lote_id == lote_id).populate_existing().all()
    lote.total_autorizadas = sum(1 for n in notas if n.status == 'emitida')
    lote.total_erro = sum(1 for n in notas if n.status == 'erro')
    todas_autorizadas = bool(notas) and len(notas) == lote.total_notas and all(n.status == 'emitida' for n in notas)
    todas_terminais = bool(notas) and len(notas) == lote.total_notas and all(
        n.status == 'emitida' or _reprocessavel(n) for n in notas)
    lote.status = 'concluido' if todas_autorizadas else ('com_erro' if todas_terminais else 'processando')
    lote.concluido_em = datetime.now(timezone.utc) if todas_terminais else None
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
    _fechar_lote(db, lote_id)
    rows = (
        db.query(NfseNota, Billing, Client)
        .join(Billing, Billing.id == NfseNota.billing_id)
        .join(Client, Client.id == Billing.client_id)
        .filter(NfseNota.lote_id == lote_id)
        .order_by(Client.name)
        .populate_existing().all()
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
            'erro_tipo': nota.erro_tipo,
            'competencia': nota.competencia.isoformat() if nota.competencia else None,
            'discriminacao': nota.discriminacao,
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
    rows = query.order_by(NfseNota.id.desc()).offset(max(offset, 0)).limit(limit).populate_existing().all()

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
            'erro_tipo': nota.erro_tipo,
            'competencia': nota.competencia.isoformat() if nota.competencia else None,
            'discriminacao': nota.discriminacao,
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
    desconhecidas = base.filter(NfseNota.status == 'desconhecido').count()
    return {
        'competencia': inicio.strftime('%m/%Y'),
        'autorizadas': autorizadas,
        'negadas': negadas,
        'processando': processando,
        'desconhecidas': desconhecidas,
        'total': autorizadas + negadas + processando + desconhecidas,
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
