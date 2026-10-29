"""Travas reais da recuperação em relação a outra recuperação e ao fechamento."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from threading import Barrier, Event
from time import monotonic, sleep

import pytest
from sqlalchemy import select, text

from app.models.billing import Billing
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus, TrackerStatus
from app.models.plan import Plan
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.services.billing_closure import execute_closure
from app.services.tracker_charge_recovery import recover_tracker_charges
from tests.test_database_concurrency_postgres import postgres_api  # noqa: F401

pytestmark = pytest.mark.postgres


@pytest.fixture()
def recovery_case(postgres_api):
    _, sessions, engine = postgres_api
    with sessions() as db:
        client = Client(name='Cliente recuperação', cpf_cnpj='52998224725', type='pf')
        plan = Plan(name='Plano teste', price=Decimal('99.90'), active=True)
        db.add_all([client, plan])
        db.flush()
        vehicle = Vehicle(client_id=client.id, plate='ABC1D23', type='passeio')
        db.add(vehicle)
        db.flush()
        tracker = Tracker(client_id=client.id, vehicle_id=vehicle.id,
            imei='123456789012345', status=TrackerStatus.INSTALLED)
        db.add(tracker)
        db.flush()
        contract = Contract(client_id=client.id, vehicle_id=vehicle.id, tracker_id=tracker.id,
            plan_id=plan.id, start_date=date(2026, 1, 1), status='ativo', billing_day=15)
        db.add(contract)
        db.flush()
        source = Billing(client_id=client.id, payer_client_id=client.id, contract_id=contract.id,
            vehicle_id=vehicle.id, tracker_id=tracker.id, billing_type='avulsa',
            title='Manutenção do rastreador', amount=Decimal('120.50'),
            due_date=date(2026, 11, 10), status=BillingStatus.PENDING,
            created_at=datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
        db.add(source)
        db.commit()
        yield sessions, client.id, source.id, engine


def test_two_recoveries_create_only_one_item(recovery_case):
    sessions, client_id, billing_id, _ = recovery_case
    barrier = Barrier(2)

    def recover():
        barrier.wait(timeout=10)
        with sessions() as db:
            [entry] = recover_tracker_charges(db, [billing_id], date(2026, 10, 1),
                apply=True, expected_client_id=client_id)
            db.commit()
            return entry

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: recover(), range(2)))
    assert sorted(row['result'] for row in results) == ['ja_recuperada', 'recuperada']
    assert results[0]['item_id'] == results[1]['item_id']
    with sessions() as db:
        assert len(db.scalars(select(ClientChargeItem)).all()) == 1
        assert db.get(Billing, billing_id).status == BillingStatus.CANCELED


def test_closure_waits_for_recovery_commit_and_includes_item(recovery_case):
    sessions, client_id, billing_id, engine = recovery_case
    ready = Event()
    release = Event()
    closure_started = Event()

    def recover():
        with sessions() as db:
            recover_tracker_charges(db, [billing_id], date(2026, 10, 1),
                apply=True, expected_client_id=client_id)
            ready.set()
            assert release.wait(timeout=10)
            db.commit()

    def close():
        closure_started.set()
        with sessions() as db:
            return execute_closure(db, date(2026, 11, 1), activity_month=date(2026, 10, 1))

    with ThreadPoolExecutor(max_workers=2) as pool:
        recovering = pool.submit(recover)
        try:
            assert ready.wait(timeout=10)
            closing = pool.submit(close)
            assert closure_started.wait(timeout=10)
            deadline = monotonic() + 5
            waiting = False
            while monotonic() < deadline:
                with engine.connect() as connection:
                    waiting = connection.scalar(text("""
                        SELECT EXISTS (
                            SELECT 1 FROM pg_locks l JOIN pg_stat_activity a USING (pid)
                            WHERE l.locktype = 'advisory' AND NOT l.granted
                              AND a.datname = current_database()
                        )
                    """))
                if waiting:
                    break
                sleep(0.02)
            assert waiting, 'O fechamento não esperou a trava da recuperação.'
            assert not closing.done()
        finally:
            release.set()
        recovering.result(timeout=10)
        result = closing.result(timeout=10)
    assert result['services_generated'] == 1
    [title_id] = result['payment_billing_ids']
    with sessions() as db:
        assert db.get(Billing, title_id).amount == Decimal('220.40')
        assert db.get(Billing, billing_id).status == BillingStatus.CANCELED
