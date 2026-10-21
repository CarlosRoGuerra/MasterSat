"""Valida o SQL pontual em schema PostgreSQL descartável, com dados fictícios."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import psycopg
from sqlalchemy import inspect

from app.models.billing import Billing
from app.models.billing_change_log import BillingChangeLog
from app.models.client import Client
from app.models.enums import BillingStatus
from tests.test_database_concurrency_postgres import postgres_api  # noqa: F401

pytestmark = pytest.mark.postgres


@pytest.fixture
def repair_case(postgres_api):
    _, sessions, engine = postgres_api
    with sessions() as db:
        owner = Client(name='FORTMAIS MATERIAIS ELETRICOS E HIDRAULICOS EIRELI', cpf_cnpj='111', type='pj')
        payer = Client(name='ACQUAFORT COMERCIO DE MATERIAIS ELETRICOS E HIDRAULICOS EIRELI', cpf_cnpj='222', type='pj')
        other = Client(name='Outro pagador', cpf_cnpj='333', type='pj')
        db.add_all([owner, payer, other]); db.flush()
        target = Billing(id=38709, client_id=owner.id, amount=Decimal('649.90'),
                         due_date=date(2026, 10, 15), status=BillingStatus.PAID,
                         payment_date=date(2026, 10, 15), paid_amount=Decimal('649.90'),
                         receipt_number='REC-PRESERVADO', title='Fechamento Fortmais', billing_type='boleto_unico')
        sibling = Billing(id=38710, client_id=owner.id, amount=Decimal('100'),
                          due_date=date(2026, 11, 15), status=BillingStatus.PENDING)
        db.add_all([target, sibling]); db.commit()
        ids = owner.id, payer.id, other.id
    return sessions, engine, ids


def _run(engine):
    sql = (Path(__file__).resolve().parents[1] / 'scripts/sql/corrigir_pasta_pagador_38709.sql').read_text(encoding='utf-8')
    sql = '\n'.join(line for line in sql.splitlines() if not line.startswith('\\'))
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
        with connection.connection.driver_connection.cursor() as cursor:
            cursor.execute(sql)
            while cursor.nextset():
                pass


def _snapshot(obj):
    return {col.key: getattr(obj, col.key) for col in inspect(obj).mapper.column_attrs}


def test_sql_fills_only_missing_payer_preserves_payment_and_is_idempotent(repair_case):
    sessions, engine, (_, payer_id, _) = repair_case
    with sessions() as db:
        before = _snapshot(db.get(Billing, 38709))
        sibling_before = _snapshot(db.get(Billing, 38710))
    _run(engine)
    _run(engine)
    with sessions() as db:
        after = _snapshot(db.get(Billing, 38709))
        assert after['payer_client_id'] == payer_id
        for field, value in before.items():
            if field not in ('payer_client_id', 'updated_at'):
                assert after[field] == value, field
        assert _snapshot(db.get(Billing, 38710)) == sibling_before
        changes = db.query(BillingChangeLog).all()
        assert len(changes) == 1
        assert changes[0].field_name == 'payer_client_id'
        assert changes[0].previous_value is None
        assert changes[0].new_value == str(payer_id)


def test_sql_already_correct_does_not_write_even_timestamp(repair_case):
    sessions, engine, (_, payer_id, _) = repair_case
    with sessions() as db:
        db.get(Billing, 38709).payer_client_id = payer_id
        db.commit()
        before = _snapshot(db.get(Billing, 38709))
    _run(engine)
    with sessions() as db:
        assert _snapshot(db.get(Billing, 38709)) == before
        assert db.query(BillingChangeLog).count() == 0


@pytest.mark.parametrize('invalid', ['other_payer', 'wrong_owner', 'wrong_amount', 'wrong_date', 'unpaid', 'deleted', 'ambiguous_payer'])
def test_sql_aborts_on_conflicting_or_unexpected_data(repair_case, invalid):
    sessions, engine, (_, _, other_id) = repair_case
    with sessions() as db:
        b = db.get(Billing, 38709)
        if invalid == 'other_payer': b.payer_client_id = other_id
        if invalid == 'wrong_owner': b.client_id = other_id
        if invalid == 'wrong_amount': b.amount = Decimal('1')
        if invalid == 'wrong_date': b.due_date = date(2026, 11, 15)
        if invalid == 'unpaid': b.status = BillingStatus.PENDING
        if invalid == 'deleted': b.is_deleted = True
        if invalid == 'ambiguous_payer':
            db.add(Client(name='ACQUAFORT COMERCIO DE MATERIAIS ELETRICOS E HIDRAULICOS OUTRA', cpf_cnpj='444', type='pj'))
        db.commit()
        before = _snapshot(b)
    expected = ('outro pagador explícito' if invalid == 'other_payer' else
                'query returned more than one row' if invalid == 'ambiguous_payer' else
                'não corresponde ao título pago')
    with pytest.raises(psycopg.Error, match=expected):
        _run(engine)
    with sessions() as db:
        assert _snapshot(db.get(Billing, 38709)) == before
        assert db.query(BillingChangeLog).count() == 0
