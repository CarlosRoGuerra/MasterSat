"""Behavior and query budgets for the first performance changes."""

from sqlalchemy import event

from app.models.client import Client
from app.models.vehicle import Vehicle
from app.models.audit_log import AuditLog
from app.core import dashboard_cache


def _selects_for_request(db, request):
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            statements.append(statement)

    event.listen(db.bind, 'before_cursor_execute', capture)
    try:
        response = request()
    finally:
        event.remove(db.bind, 'before_cursor_execute', capture)
    return response, statements


def test_dashboard_uses_aggregated_queries(http, db):
    admin, admin_queries = _selects_for_request(db, lambda: http.get('/api/v1/dashboard/'))
    assert admin.status_code == 200
    assert len(admin_queries) <= 8
    assert admin.json()['finance'] is not None



def test_operational_dashboard_skips_finance_queries(http_op, db):
    operational, operational_queries = _selects_for_request(db, lambda: http_op.get('/api/v1/dashboard/'))
    assert operational.status_code == 200
    assert len(operational_queries) <= 5
    assert operational.json()['finance'] is None
    assert operational.json()['upcoming_billings'] == []


def test_client_page_sorts_and_counts_only_live_vehicles(http, db, cliente):
    another = Client(name='AAA Primeiro', cpf_cnpj='11122233344', type='pj', status='ativo')
    db.add(another)
    db.flush()
    db.add_all([
        Vehicle(client_id=another.id, plate='AAA0001'),
        Vehicle(client_id=another.id, plate='AAA0002', is_deleted=True),
    ])
    db.commit()

    first, queries = _selects_for_request(
        db, lambda: http.get('/api/v1/clients/', params={
            'sort': 'name', 'direction': 'asc', 'skip': 0, 'limit': 1,
        })
    )
    assert first.status_code == 200
    assert first.json()['total'] == 2
    assert [item['name'] for item in first.json()['items']] == ['AAA Primeiro']
    assert first.json()['items'][0]['vehicle_count'] == 1
    assert len(queries) <= 4

    second = http.get('/api/v1/clients/', params={
        'sort': 'name', 'direction': 'asc', 'skip': 1, 'limit': 1,
    })
    assert second.status_code == 200
    assert second.json()['items'][0]['id'] == cliente.id


def test_client_summary_respects_search_filters(http, db, cliente):
    db.add(Client(name='Empresa Alfa', cpf_cnpj='11122233344', type='pj', status='inadimplente'))
    db.commit()
    response = http.get('/api/v1/clients/summary')
    assert response.status_code == 200
    assert response.json() == {'total': 2, 'active': 1, 'delinquent': 1, 'company': 1}
    filtered = http.get('/api/v1/clients/summary', params={'search': 'Empresa', 'type': 'pj'})
    assert filtered.json() == {'total': 1, 'active': 0, 'delinquent': 1, 'company': 1}


def test_client_summary_requires_registry_role(http_cliente):
    assert http_cliente.get('/api/v1/clients/summary').status_code == 403


def test_client_sort_is_allowlisted(http):
    assert http.get('/api/v1/clients/', params={'sort': 'password_hash'}).status_code == 422
    assert http.get('/api/v1/clients/', params={'direction': 'sideways'}).status_code == 422


def test_request_timing_exposes_trace_without_sensitive_data(http, cliente):
    response = http.get('/api/v1/clients/')
    assert response.status_code == 200
    assert len(response.headers['x-request-id']) == 32
    timing = response.headers['server-timing']
    assert timing.startswith('app;dur=')
    assert 'queries=4' in timing
    assert cliente.cpf_cnpj not in timing


def test_dashboard_cache_is_separate_by_role_and_invalidates(monkeypatch):
    values = {}

    class FakeRedis:
        def get(self, key):
            return values.get(key)

        def setex(self, key, ttl, value):
            assert ttl == 20
            values[key] = value

        def delete(self, *keys):
            for key in keys:
                values.pop(key, None)

    monkeypatch.setattr(dashboard_cache, '_enabled', lambda: True)
    monkeypatch.setattr(dashboard_cache, '_redis', FakeRedis())
    dashboard_cache.put_dashboard('admin', {'finance': {'pending_count': 1}})
    assert dashboard_cache.get_dashboard('admin')['finance']['pending_count'] == 1
    assert dashboard_cache.get_dashboard('operacional') is None
    dashboard_cache.invalidate_dashboard()
    assert dashboard_cache.get_dashboard('admin') is None


def test_dashboard_cache_hit_and_mutation_invalidation(http, db, monkeypatch):
    values = {}

    class FakeRedis:
        def get(self, key):
            return values.get(key)

        def setex(self, key, _ttl, value):
            values[key] = value

        def delete(self, *keys):
            for key in keys:
                values.pop(key, None)

    monkeypatch.setattr(dashboard_cache, '_enabled', lambda: True)
    monkeypatch.setattr(dashboard_cache, '_redis', FakeRedis())
    first = http.get('/api/v1/dashboard/')
    second = http.get('/api/v1/dashboard/')
    assert first.headers['x-dashboard-cache'] == 'MISS'
    assert second.headers['x-dashboard-cache'] == 'HIT'
    assert first.json() == second.json()

    created = http.post('/api/v1/clients/', json={
        'name': 'Cliente Cache', 'cpf_cnpj': '11122233344', 'type': 'pf',
    })
    assert created.status_code == 200
    assert http.get('/api/v1/dashboard/').headers['x-dashboard-cache'] == 'MISS'


def test_audit_page_limits_rows_in_sql_and_keeps_legacy_contract(http, db):
    db.add_all([
        AuditLog(event_id=f'perf-{i}', user_id=1, user_name='Admin', user_role='admin',
                 method='GET' if i % 2 else 'POST', path='/api/v1/clients/',
                 entity_type='cliente', status_code=200, description=f'Evento {i}')
        for i in range(60)
    ])
    db.commit()
    first = http.get('/api/v1/audit-logs/paged', params={'limit': 50})
    assert first.status_code == 200
    assert first.json()['total'] == 60
    assert len(first.json()['items']) == 50
    second = http.get('/api/v1/audit-logs/paged', params={'skip': 50, 'limit': 50})
    assert len(second.json()['items']) == 10
    assert set(item['id'] for item in first.json()['items']).isdisjoint(
        item['id'] for item in second.json()['items']
    )
    filtered = http.get('/api/v1/audit-logs/paged', params={'method': 'POST', 'search': 'Evento 2'})
    assert filtered.json()['total'] == 6
    legacy = http.get('/api/v1/audit-logs/', params={'limit': 10})
    assert isinstance(legacy.json(), list)
    assert len(legacy.json()) == 10


def test_audit_paged_is_admin_only(http_op):
    assert http_op.get('/api/v1/audit-logs/paged').status_code == 403
