"""At-least-once Redis stream for audit events with idempotent batch writes."""

import json
import logging
import os
import socket
import threading
import time
from datetime import datetime

from redis import Redis, RedisError, ResponseError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.config import settings

logger = logging.getLogger(__name__)
STREAM = 'mastersat:audit:v1'
DEAD_STREAM = 'mastersat:audit:dead:v1'
GROUP = 'mastersat-audit-workers'
_COLUMNS = (
    'event_id', 'user_id', 'user_name', 'user_role', 'method', 'path',
    'entity_type', 'entity_id', 'status_code', 'ip_address', 'description',
)
_producer = Redis.from_url(
    settings.audit_redis_url, decode_responses=True,
    socket_connect_timeout=1, socket_timeout=2,
)


def enqueue_event(event: dict) -> None:
    """Raises on Redis failure so the caller can write directly to PostgreSQL."""
    _producer.xadd(STREAM, {'event': json.dumps(event, ensure_ascii=False)})


def _persist_batch(events: list[dict]) -> None:
    from app.db.session import SessionLocal
    from app.models.audit_log import AuditLog
    from app.models.user import User

    db = SessionLocal()
    try:
        missing_user_ids = {
            e['user_id'] for e in events
            if e.get('user_id') and (not e.get('user_name') or not e.get('user_role'))
        }
        users = {}
        if missing_user_ids:
            users = {user.id: user for user in db.scalars(
                select(User).where(User.id.in_(missing_user_ids))
            ).all()}
        rows = []
        for event in events:
            row = {key: event.get(key) for key in _COLUMNS}
            user = users.get(row.get('user_id'))
            if user:
                row['user_name'] = row.get('user_name') or user.name
                row['user_role'] = row.get('user_role') or (
                    user.role.value if hasattr(user.role, 'value') else str(user.role)
                )
            row['created_at'] = datetime.fromisoformat(event['created_at'])
            rows.append(row)
        insert = sqlite_insert if db.bind.dialect.name == 'sqlite' else pg_insert
        db.execute(insert(AuditLog).values(rows).on_conflict_do_nothing(
            index_elements=[AuditLog.event_id],
            index_where=AuditLog.event_id.isnot(None),
        ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _ack_and_delete(client: Redis, ids: list[str]) -> None:
    if not ids:
        return
    pipe = client.pipeline(transaction=True)
    pipe.xack(STREAM, GROUP, *ids)
    pipe.xdel(STREAM, *ids)
    pipe.execute()


def _process_batch(client: Redis, messages: list[tuple[str, dict]]) -> None:
    valid = []
    invalid = []
    for message_id, fields in messages:
        try:
            event = json.loads(fields['event'])
            if (not isinstance(event, dict) or not isinstance(event.get('event_id'), str)
                    or not event['event_id']
                    or not isinstance(event.get('method'), str)
                    or not isinstance(event.get('path'), str)
                    or not isinstance(event.get('status_code'), int)):
                raise ValueError('invalid required fields')
            datetime.fromisoformat(event['created_at'])
            valid.append((message_id, event))
        except (KeyError, ValueError, TypeError):
            invalid.append((message_id, fields))

    for message_id, fields in invalid:
        # Never acknowledge a poison record unless it was saved to dead-letter.
        client.xadd(DEAD_STREAM, {
            'stream_id': message_id,
            'event': fields.get('event', ''),
            'reason': 'invalid audit event',
        })
        logger.error('Invalid audit event moved to dead-letter stream id=%s', message_id)
        _ack_and_delete(client, [message_id])

    if valid:
        _persist_batch([event for _, event in valid])
        _ack_and_delete(client, [message_id for message_id, _ in valid])
        logger.debug('Persisted %s audit events in one transaction', len(valid))


def run_audit_worker(stop: threading.Event) -> None:
    """Recover pending messages, then read new ones in batches of 50."""
    consumer = f'{socket.gethostname()}-{os.getpid()}'
    client = Redis.from_url(
        settings.audit_redis_url, decode_responses=True,
        socket_connect_timeout=1, socket_timeout=2,
    )
    group_ready = False
    last_metrics = 0.0
    while not stop.is_set():
        try:
            if not group_ready:
                try:
                    client.xgroup_create(STREAM, GROUP, id='0-0', mkstream=True)
                except ResponseError as exc:
                    if 'BUSYGROUP' not in str(exc):
                        raise
                group_ready = True

            # Recover work from a crashed consumer after 30 seconds. An event
            # committed before a crash is ignored by the unique event_id.
            _next, pending, _deleted = client.xautoclaim(
                STREAM, GROUP, consumer, min_idle_time=30_000,
                start_id='0-0', count=50,
            )
            if pending:
                _process_batch(client, pending)
                continue

            batches = client.xreadgroup(
                GROUP, consumer, {STREAM: '>'}, count=50, block=1000,
            )
            for _stream, messages in batches:
                # Give concurrent requests a short window to join this batch.
                # Otherwise XREADGROUP wakes at the first event and low/medium
                # traffic still produces one PostgreSQL commit per event.
                if len(messages) < 50:
                    more = client.xreadgroup(
                        GROUP, consumer, {STREAM: '>'}, count=50 - len(messages), block=100,
                    )
                    for _more_stream, extra in more:
                        messages.extend(extra)
                _process_batch(client, messages)
            if time.monotonic() - last_metrics >= 60:
                logger.info('Audit queue depth=%s pending=%s', client.xlen(STREAM),
                            client.xpending(STREAM, GROUP)['pending'])
                last_metrics = time.monotonic()
        except RedisError:
            group_ready = False
            logger.exception('Audit queue unavailable; retrying. Producers use database fallback.')
            stop.wait(2)
        except Exception:
            # Keep messages pending for retry; no acknowledgement on DB failure.
            logger.exception('Audit batch failed; events remain pending for retry')
            stop.wait(2)
