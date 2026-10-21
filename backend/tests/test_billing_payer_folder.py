"""A pasta de boletos segue o pagador histórico, sem transferir contratos."""
from datetime import date
from decimal import Decimal
from io import BytesIO, StringIO
import csv

import pytest
from openpyxl import load_workbook
from sqlalchemy import inspect

from app.models.ailos_boleto import AilosBoleto
from app.models.ailos_lote import AilosLote
from app.models.billing import Billing
from app.models.enums import BillingStatus


@pytest.fixture
def wallet(db, cliente, outro_cliente, contrato):
    contrato.interveniente_client_id = outro_cliente.id
    rows = [
        Billing(client_id=cliente.id, payer_client_id=outro_cliente.id,
                contract_id=contrato.id, billing_type='boleto_unico', title='Fechamento Fortmais',
                amount=Decimal('649.90'), due_date=date(2026, 10, 15),
                status=BillingStatus.PAID, payment_date=date(2026, 10, 15),
                paid_amount=Decimal('649.90'), receipt_number='REC-FORTMAIS', period_label='09/2026'),
        Billing(client_id=cliente.id, payer_client_id=outro_cliente.id,
                amount=Decimal('64.99'), due_date=date(2026, 11, 15), status=BillingStatus.PENDING),
        Billing(client_id=cliente.id, payer_client_id=cliente.id,
                amount=Decimal('30'), due_date=date(2026, 10, 15), status=BillingStatus.PENDING),
        Billing(client_id=outro_cliente.id, payer_client_id=None,
                amount=Decimal('10'), due_date=date(2026, 10, 15), status=BillingStatus.PAID),
    ]
    db.add_all(rows); db.flush()
    component = Billing(client_id=cliente.id, payer_client_id=outro_cliente.id,
                        amount=Decimal('64.99'), due_date=date(2026, 10, 15),
                        status=BillingStatus.CANCELED, substituted_by_id=rows[0].id)
    removed = Billing(client_id=cliente.id, payer_client_id=outro_cliente.id,
                      amount=Decimal('99'), due_date=date(2026, 10, 15),
                      status=BillingStatus.PENDING, is_deleted=True)
    db.add_all([component, removed]); db.commit()
    return [*rows, component, removed]


def _ids(response):
    assert response.status_code == 200, response.text
    return {item['id'] for item in response.json()}


def test_paid_and_open_titles_belong_to_payer_folder(http, wallet, cliente, outro_cliente):
    assert _ids(http.get('/api/v1/billings/', params={'payer_client_id': outro_cliente.id})) == {
        wallet[0].id, wallet[1].id, wallet[3].id,
    }
    assert _ids(http.get('/api/v1/billings/', params={'payer_client_id': cliente.id})) == {wallet[2].id}


def test_owner_filter_still_queries_operational_origin(http, wallet, cliente):
    assert _ids(http.get('/api/v1/billings/', params={'client_id': cliente.id})) == {
        row.id for row in wallet[:3]
    }


def test_payer_filter_combines_with_owner_status_and_period(http, wallet, cliente, outro_cliente):
    params = {'payer_client_id': outro_cliente.id, 'client_id': cliente.id,
              'status': 'paga', 'period_label': '09/2026', 'due_from': '2026-10-01', 'due_to': '2026-10-31'}
    assert _ids(http.get('/api/v1/billings/', params=params)) == {wallet[0].id}
    params['client_id'] = outro_cliente.id
    assert _ids(http.get('/api/v1/billings/', params=params)) == set()


def test_current_contract_payer_does_not_move_historical_titles(http, db, wallet, contrato, cliente, outro_cliente):
    contrato.interveniente_client_id = cliente.id
    outro_cliente.is_deleted = True
    db.commit()
    assert wallet[0].id in _ids(http.get('/api/v1/billings/', params={'payer_client_id': outro_cliente.id}))
    assert wallet[0].id not in _ids(http.get('/api/v1/billings/', params={'payer_client_id': cliente.id}))


def test_list_does_not_rewrite_paid_billing_or_contract(http, db, wallet, contrato, outro_cliente):
    def snapshot(obj):
        return {column.key: getattr(obj, column.key) for column in inspect(obj).mapper.column_attrs}
    before = snapshot(wallet[0]), snapshot(contrato)
    http.get('/api/v1/billings/', params={'payer_client_id': outro_cliente.id})
    db.expire_all()
    assert (snapshot(wallet[0]), snapshot(contrato)) == before


def test_explicit_component_lookup_stays_available(http, wallet, outro_cliente):
    ids = _ids(http.get('/api/v1/billings/', params={'payer_client_id': outro_cliente.id, 'include_substituted': True}))
    assert wallet[4].id in ids
    assert wallet[5].id not in ids


@pytest.mark.parametrize('format', ['csv', 'xlsx'])
def test_exports_match_payer_folder(http, wallet, outro_cliente, format):
    result = http.get(f'/api/v1/billings/exports/{format}', params={'payer_client_id': outro_cliente.id})
    assert result.status_code == 200, result.text
    if format == 'csv':
        rows = list(csv.reader(StringIO(result.content.decode('utf-8-sig'))))
    else:
        rows = list(load_workbook(BytesIO(result.content), read_only=True).active.values)
    assert {int(row[0]) for row in rows[1:]} == {wallet[i].id for i in (0, 1, 3)}


@pytest.mark.parametrize('route', ['/billings/', '/billings/exports/csv', '/billings/exports/xlsx', '/boletos/carne'])
def test_rejects_invalid_payer_id(http, route):
    assert http.get('/api/v1' + route, params={'payer_client_id': 0}).status_code == 422


@pytest.mark.parametrize('profile', ['http_op', 'http_cliente', 'http_unauth'])
def test_payer_filter_keeps_financial_permissions(request, profile, outro_cliente):
    response = request.getfixturevalue(profile).get('/api/v1/billings/', params={'payer_client_id': outro_cliente.id})
    assert response.status_code in (401, 403)


def _carne(db, billing, ticket):
    lote = AilosLote(tipo='carne', ticket=ticket, numero_convenio='teste',
                     billing_ids=[billing.id], status='done')
    db.add(lote); db.flush()
    db.add(AilosBoleto(billing_id=billing.id, lote_id=lote.id, numero_convenio='teste'))
    db.commit()
    return lote


def test_carnets_follow_payer_and_owner_filter_remains_compatible(http, db, wallet, cliente, outro_cliente):
    delegated = _carne(db, wallet[1], 'delegated')
    own = _carne(db, wallet[2], 'own')
    legacy = _carne(db, wallet[3], 'legacy')
    removed = _carne(db, wallet[5], 'removed')
    def ids(**params):
        r = http.get('/api/v1/boletos/carne', params=params)
        assert r.status_code == 200, r.text
        return {item['lote_id'] for item in r.json()}
    assert ids(payer_client_id=outro_cliente.id) == {delegated.id, legacy.id}
    assert ids(payer_client_id=cliente.id) == {own.id}
    assert ids(client_id=cliente.id) == {delegated.id, own.id}
    assert ids(client_id=cliente.id, payer_client_id=outro_cliente.id) == {delegated.id}
    assert removed.id not in ids(payer_client_id=outro_cliente.id)


def test_carnets_still_require_a_customer_filter(http):
    assert http.get('/api/v1/boletos/carne').status_code == 422
