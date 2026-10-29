"""A migração preserva as datas existentes sem inferir seu significado."""
import importlib.util
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from tests.test_database_concurrency_postgres import postgres_api  # noqa: F401

pytestmark = pytest.mark.postgres


def test_date_basis_migration_preserves_existing_rows_and_validates_values(postgres_api):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    _, sessions, engine = postgres_api
    with sessions() as db:
        client = Client(name='Cliente teste migração', cpf_cnpj='52998224725', type='pf')
        db.add(client)
        db.flush()
        db.add(ClientChargeItem(client_id=client.id, title='Adicional existente',
            quantity=1, unit_price=5, total_amount=5, installment_count=1,
            start_date=date(2026, 11, 10), active=True))
        db.commit()
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/d6a4e9c27b10_charge_item_date_basis.py'
    spec = importlib.util.spec_from_file_location('charge_date_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.downgrade()  # estrutura anterior, contendo um lançamento real
        before = connection.execute(text('SELECT id, start_date, total_amount, active FROM client_charge_items')).all()
        migration.upgrade()
        after = connection.execute(text('SELECT id, start_date, total_amount, active FROM client_charge_items')).all()
        assert after == before
        assert connection.scalar(text('SELECT date_basis FROM client_charge_items')) == 'service_date'
        column = next(c for c in inspect(connection).get_columns('client_charge_items') if c['name'] == 'date_basis')
        assert column['nullable'] is False
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text("UPDATE client_charge_items SET date_basis = 'invalida'"))
