"""Fila durável (outbox) das sincronizações com o Multiportal.

Antes, o fluxo completo — cliente, usuário, veículo, equipamento e vínculos —
rodava sequencialmente dentro da requisição HTTP. Se o provedor caísse no meio,
parte dos dados existia lá fora e parte não, e o reprocessamento dependia de
alguém perceber o status vermelho e clicar em "reprocessar".

Aqui a intenção de sincronizar é persistida na mesma transação que alterou o
dado (padrão outbox) e um worker a consome com retry exponencial. Uma tarefa de
reconciliação varre rastreadores que ficaram pendentes sem item na fila — a
rede de segurança para intenções perdidas antes desta fila existir.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.multiportal_outbox import MultiportalOutbox
from app.models.tracker import Tracker

# Estados que ainda serão trabalhados pelo worker.
ACTIVE_STATUSES = ('pending', 'processing')

# Backoff exponencial: 1min, 2, 4, 8, 16, 32, 60, 60... Cobre desde uma
# indisponibilidade momentânea até uma janela de manutenção de horas, sem
# martelar o provedor.
BASE_BACKOFF = timedelta(minutes=1)
MAX_BACKOFF = timedelta(minutes=60)
MAX_ATTEMPTS = 8

# Uma tentativa que começou e nunca terminou (deploy/restart no meio) fica
# presa em 'processing'. Depois deste tempo ela é considerada órfã e volta
# para a fila.
STALE_PROCESSING = timedelta(minutes=15)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    """SQLite devolve datetime sem tzinfo; Postgres devolve com."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def backoff_for(attempts: int) -> timedelta:
    """Espera antes da próxima tentativa, limitada a MAX_BACKOFF."""
    if attempts <= 0:
        return timedelta(0)
    # O expoente é limitado ANTES da multiplicação: 2**n com n grande estoura o
    # timedelta (OverflowError) e derrubaria o processamento da fila. Hoje
    # attempts para em MAX_ATTEMPTS, mas a função não pode depender disso.
    expoente = min(attempts - 1, 20)
    delay = BASE_BACKOFF * (2 ** expoente)
    return min(delay, MAX_BACKOFF)


def enqueue_full_sync(
    db: Session,
    tracker_id: int,
    *,
    reason: str | None = None,
    flush: bool = True,
) -> MultiportalOutbox:
    """Enfileira (ou reaproveita) o fluxo completo de um rastreador.

    Idempotente por rastreador: se já existe um item ativo, ele é reaproveitado
    em vez de criar um segundo — dez edições seguidas do mesmo cliente geram uma
    sincronização, não dez.

    Não comita: quem chamou é que decide, para que o enfileiramento faça parte
    da mesma transação da alteração do dado. Se aquela transação falhar, a
    intenção some junto — que é exatamente o comportamento desejado.
    """
    existing = db.scalar(
        select(MultiportalOutbox)
        .where(
            MultiportalOutbox.tracker_id == tracker_id,
            MultiportalOutbox.operation == 'full_sync',
            MultiportalOutbox.status.in_(ACTIVE_STATUSES),
        )
        .order_by(MultiportalOutbox.id.asc())
    )
    if existing is not None:
        # O dado mudou de novo: a próxima tentativa deve considerar o valor
        # novo, então antecipa a execução e limpa o backoff acumulado.
        existing.next_attempt_at = _now()
        if reason:
            existing.reason = reason
        if flush:
            db.flush()
        return existing

    item = MultiportalOutbox(
        tracker_id=tracker_id,
        operation='full_sync',
        status='pending',
        attempts=0,
        next_attempt_at=_now(),
        reason=reason,
    )
    db.add(item)
    if flush:
        db.flush()
    return item


def claim_due_items(db: Session, limit: int = 20) -> list[MultiportalOutbox]:
    """Reserva itens prontos para processar, marcando-os como 'processing'.

    A reserva é comitada antes do trabalho começar: assim dois workers (ou dois
    ciclos do mesmo worker) não pegam o mesmo item. O advisory lock do worker já
    serializa, mas isto mantém a fila correta se um dia houver mais de um.
    """
    agora = _now()
    itens = list(
        db.scalars(
            select(MultiportalOutbox)
            .where(
                MultiportalOutbox.status == 'pending',
                MultiportalOutbox.next_attempt_at <= agora,
            )
            .order_by(MultiportalOutbox.next_attempt_at.asc(), MultiportalOutbox.id.asc())
            .limit(limit)
        ).all()
    )
    for item in itens:
        item.status = 'processing'
    db.commit()
    return itens


def requeue_stale_processing(db: Session) -> int:
    """Devolve à fila itens presos em 'processing' (restart no meio da tentativa)."""
    limite = _now() - STALE_PROCESSING
    presos = list(
        db.scalars(
            select(MultiportalOutbox).where(MultiportalOutbox.status == 'processing')
        ).all()
    )
    devolvidos = 0
    for item in presos:
        atualizado = _as_aware(item.updated_at) or _as_aware(item.created_at)
        if atualizado is not None and atualizado > limite:
            continue
        item.status = 'pending'
        item.next_attempt_at = _now()
        item.last_error = 'Tentativa interrompida (reinício do serviço) — reenfileirada.'
        devolvidos += 1
    if devolvidos:
        db.commit()
    return devolvidos


def mark_done(db: Session, item: MultiportalOutbox, *, batch_id: str | None = None) -> None:
    item.status = 'done'
    item.attempts += 1
    item.completed_at = _now()
    item.last_error = None
    if batch_id:
        item.batch_id = batch_id
    db.commit()


def mark_failed_attempt(
    db: Session,
    item: MultiportalOutbox,
    erro: str,
    *,
    batch_id: str | None = None,
) -> None:
    """Registra a falha e reagenda — ou desiste, se as tentativas acabaram.

    'failed' é terminal de propósito: erro que sobrevive a 8 tentativas ao longo
    de horas costuma ser dado inválido, não indisponibilidade. Insistir para
    sempre esconderia o problema em vez de expô-lo.
    """
    item.attempts += 1
    item.last_error = (erro or '')[:2000]
    if batch_id:
        item.batch_id = batch_id
    if item.attempts >= MAX_ATTEMPTS:
        item.status = 'failed'
        item.completed_at = _now()
    else:
        item.status = 'pending'
        item.next_attempt_at = _now() + backoff_for(item.attempts)
    db.commit()


def reconcile_pending_trackers(db: Session, limit: int = 200) -> int:
    """Enfileira rastreadores pendentes/com erro que não têm item ativo na fila.

    Rede de segurança para o que escapou: registros marcados antes desta fila
    existir, ou uma intenção perdida por um caminho que ainda não enfileira.
    Itens 'failed' não são ressuscitados aqui — eles pedem intervenção.
    """
    ativos = select(MultiportalOutbox.tracker_id).where(
        MultiportalOutbox.status.in_(ACTIVE_STATUSES)
    )
    candidatos = list(
        db.scalars(
            select(Tracker)
            .where(
                Tracker.is_deleted.is_(False),
                Tracker.vehicle_id.isnot(None),
                or_(
                    Tracker.integration_status == 'pendente',
                    Tracker.integration_status == 'erro',
                ),
                Tracker.id.notin_(ativos),
            )
            .order_by(Tracker.id.asc())
            .limit(limit)
        ).all()
    )
    for tracker in candidatos:
        enqueue_full_sync(db, tracker.id, reason='reconciliação', flush=False)
    if candidatos:
        db.commit()
    return len(candidatos)


def _entity_for(operation: str, *, tracker_id: int, vehicle_id: int, client_id: int) -> tuple[str, int]:
    """Mapeia a etapa do fluxo para a entidade que ela representa no log."""
    if operation == 'sincronizaCliente':
        return 'client', client_id
    if operation in {'sincronizaVeiculo', 'vinculoVeiculoCliente'}:
        return 'vehicle', vehicle_id
    return 'tracker', tracker_id


def process_item(db: Session, item: MultiportalOutbox) -> bool:
    """Executa um item da fila. Retorna True se o fluxo completou com sucesso.

    Erros são capturados e viram nova tentativa (ou falha terminal): o worker
    não pode morrer porque o provedor recusou uma chamada.
    """
    from uuid import uuid4

    from app.models.client import Client
    from app.models.enums import UserRole
    from app.models.integration_log import IntegrationLog
    from app.models.user import User
    from app.models.vehicle import Vehicle
    from app.services.multiportal import multiportal_service

    batch_id = uuid4().hex
    tracker = db.get(Tracker, item.tracker_id)
    if tracker is None or tracker.is_deleted:
        # O rastreador sumiu depois de enfileirado — nada a sincronizar.
        item.status = 'done'
        item.completed_at = _now()
        item.last_error = 'Rastreador removido antes do processamento.'
        db.commit()
        return True

    if not tracker.vehicle_id:
        # Desvinculado depois de enfileirado: o fluxo completo exige veículo, e
        # a remoção do vínculo externo é tratada pelo caminho de desinstalação.
        item.status = 'done'
        item.completed_at = _now()
        item.last_error = 'Rastreador sem veículo no momento do processamento.'
        db.commit()
        return True

    vehicle = db.get(Vehicle, tracker.vehicle_id)
    client = db.get(Client, vehicle.client_id) if vehicle else None
    if vehicle is None or vehicle.is_deleted or client is None or client.is_deleted:
        mark_failed_attempt(db, item, 'Veículo ou cliente indisponível para sincronizar.', batch_id=batch_id)
        return False

    linked_user = db.scalar(
        select(User).where(
            User.client_id == client.id,
            User.role == UserRole.CLIENT,
            User.is_deleted.is_(False),
        )
    )

    try:
        steps = multiportal_service.full_sync_for_tracker(
            tracker=tracker, vehicle=vehicle, local_client=client, linked_user=linked_user,
        )
    except Exception as exc:  # noqa: BLE001 — indisponibilidade vira retry, não crash do worker
        tracker.integration_status = 'erro'
        db.flush()
        mark_failed_attempt(db, item, str(exc), batch_id=batch_id)
        return False

    for result in steps:
        entity_type, entity_id = _entity_for(
            result.operation, tracker_id=tracker.id, vehicle_id=vehicle.id, client_id=client.id,
        )
        db.add(
            IntegrationLog(
                provider='multiportal',
                batch_id=batch_id,
                entity_type=entity_type,
                entity_id=entity_id,
                operation=result.operation,
                transaction_id=result.transaction_id,
                success=result.success,
                response_code=result.status_code,
                response_description=result.status_description,
                response_payload=result.response_payload,
            )
        )

    sucesso = bool(steps) and all(step.success for step in steps)
    tracker.integration_status = 'sincronizado' if sucesso else 'erro'
    if steps:
        tracker.integration_last_code = steps[-1].status_code
        tracker.integration_last_description = steps[-1].status_description
        tracker.integration_last_transaction_id = steps[-1].transaction_id
    db.flush()

    if sucesso:
        mark_done(db, item, batch_id=batch_id)
        return True

    passo_ruim = next((s for s in steps if not s.success), None)
    detalhe = (
        f'{passo_ruim.operation}: [{passo_ruim.status_code}] {passo_ruim.status_description}'
        if passo_ruim else 'Fluxo não retornou etapas.'
    )
    mark_failed_attempt(db, item, detalhe, batch_id=batch_id)
    return False


def run_once(db: Session, limit: int = 20) -> dict:
    """Um ciclo do worker: devolve órfãos à fila, processa o que está pronto."""
    reenfileirados = requeue_stale_processing(db)
    itens = claim_due_items(db, limit=limit)
    ok = 0
    falhas = 0
    for item in itens:
        if process_item(db, item):
            ok += 1
        else:
            falhas += 1
    return {
        'processados': len(itens),
        'sucesso': ok,
        'falhas': falhas,
        'reenfileirados': reenfileirados,
    }


def queue_stats(db: Session) -> dict:
    """Contagem por status — usada pela tela de integração e pelo alerta."""
    from sqlalchemy import func

    linhas = db.execute(
        select(MultiportalOutbox.status, func.count(MultiportalOutbox.id))
        .group_by(MultiportalOutbox.status)
    ).all()
    contagem = {status: total for status, total in linhas}
    return {
        'pending': contagem.get('pending', 0),
        'processing': contagem.get('processing', 0),
        'done': contagem.get('done', 0),
        'failed': contagem.get('failed', 0),
    }
