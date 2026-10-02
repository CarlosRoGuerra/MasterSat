from datetime import datetime, timezone
from uuid import uuid4

from redis import RedisError
from sqlalchemy import event

from app.core.audit import _write_log
from app.core.audit_queue import DEAD_STREAM, _persist_batch, _process_batch
from app.core.config import settings
from app.models.audit_log import AuditLog


def _event(index: int) -> dict:
    return {
        'event_id': str(uuid4()),
        'created_at': datetime.now(timezone.utc).isoformat(),
        'user_id': 1,
        'user_name': 'Test Admin',
        'user_role': 'admin',
        'method': 'GET',
        'path': '/api/v1/dashboard/',
        'entity_type': 'dashboard',
        'entity_id': None,
        'status_code': 200,
        'ip_address': '127.0.0.1',
        'description': f'Consultou dashboard {index}',
    }


def test_fifty_events_use_one_insert_and_are_idempotent(db):
    rows = [_event(i) for i in range(50)]
    inserts = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith('INSERT'):
            inserts.append(statement)

    event.listen(db.bind, 'before_cursor_execute', capture)
    try:
        _persist_batch(rows)
    finally:
        event.remove(db.bind, 'before_cursor_execute', capture)
    assert len(inserts) == 1
    assert db.query(AuditLog).count() == 50

    # A commit followed by a crash before XACK causes redelivery.
    _persist_batch(rows)
    assert db.query(AuditLog).count() == 50


def test_queue_failure_falls_back_to_database(db, monkeypatch):
    monkeypatch.setattr(settings, 'database_url', 'postgresql+psycopg://unused')

    def unavailable(_event):
        raise RedisError('unavailable')

    monkeypatch.setattr('app.core.audit_queue.enqueue_event', unavailable)
    _write_log(1, 'Admin', 'admin', 'POST', '/api/v1/clients/', 'cliente', None, 200,
               '127.0.0.1', 'Criou cliente')
    row = db.query(AuditLog).one()
    assert row.event_id
    assert row.method == 'POST'


def test_successful_enqueue_avoids_database_commit(db, monkeypatch):
    monkeypatch.setattr(settings, 'database_url', 'postgresql+psycopg://unused')
    queued = []
    monkeypatch.setattr('app.core.audit_queue.enqueue_event', queued.append)
    _write_log(1, 'Admin', 'admin', 'GET', '/api/v1/dashboard/', 'dashboard', None,
               200, '127.0.0.1', 'Consultou dashboard')
    assert len(queued) == 1
    assert queued[0]['event_id']
    assert db.query(AuditLog).count() == 0


def test_invalid_event_is_saved_to_dead_letter_before_ack():
    operations = []

    class FakePipeline:
        def xack(self, *args):
            operations.append(('ack', args))
            return self

        def xdel(self, *args):
            operations.append(('delete', args))
            return self

        def execute(self):
            operations.append(('execute', None))

    class FakeRedis:
        def xadd(self, stream, data):
            operations.append(('dead', stream, data))

        def pipeline(self, transaction):
            assert transaction is True
            return FakePipeline()

    _process_batch(FakeRedis(), [('1-0', {'event': '{invalid json'})])
    assert operations[0][0:2] == ('dead', DEAD_STREAM)
    assert operations[1][0] == 'ack'


def test_batch_ack_happens_only_after_persist(monkeypatch):
    operations = []

    class FakePipeline:
        def xack(self, *_args):
            operations.append('ack')
            return self

        def xdel(self, *_args):
            return self

        def execute(self):
            return None

    class FakeRedis:
        def pipeline(self, transaction):
            assert transaction is True
            return FakePipeline()

    monkeypatch.setattr('app.core.audit_queue._persist_batch',
                        lambda _events: operations.append('commit'))
    import json
    _process_batch(FakeRedis(), [('1-0', {'event': json.dumps(_event(1))})])
    assert operations == ['commit', 'ack']
