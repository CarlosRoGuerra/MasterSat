import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_recipient_migration_preserves_long_delivery_history():
    path = Path(__file__).parents[1] / 'alembic/versions/d6a4e9f21870_closure_multiple_recipients.py'
    spec = importlib.util.spec_from_file_location('recipient_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(sa.text('CREATE TABLE closure_email_deliveries (id INTEGER PRIMARY KEY, recipient VARCHAR(180))'))
        connection.execute(sa.text("INSERT INTO closure_email_deliveries VALUES (1, 'original@example.test')"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert isinstance(sa.inspect(connection).get_columns('closure_email_deliveries')[1]['type'], sa.Text)
        assert connection.execute(sa.text('SELECT recipient FROM closure_email_deliveries')).scalar() == 'original@example.test'
        recipients = ', '.join(f"{'contabilidade' * 3}{i}@example.test" for i in range(6))
        connection.execute(sa.text('UPDATE closure_email_deliveries SET recipient = :value'), {'value': recipients})
        with pytest.raises(RuntimeError, match='downgrade'):
            migration.downgrade()
        assert connection.execute(sa.text('SELECT recipient FROM closure_email_deliveries')).scalar() == recipients
        connection.execute(sa.text("UPDATE closure_email_deliveries SET recipient = 'original@example.test'"))
        migration.downgrade()
        assert sa.inspect(connection).get_columns('closure_email_deliveries')[1]['type'].length == 180
