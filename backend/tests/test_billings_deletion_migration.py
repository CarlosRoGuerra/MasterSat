"""Migração da exclusão de canceladas: legado liberado e reservas protegidas."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.models.enums import BillingStatus
from tests.fase03_apoio import cobranca


def test_upgrade_preserves_old_deletions_and_downgrade_protects_new_reservations(db, contrato):
    connection = db.connection()
    for name in ('uq_billings_contract_competencia_recorrente', 'uq_billings_item_parcela_efetiva'):
        connection.execute(text(f'DROP INDEX {name}'))
    connection.execute(text("CREATE UNIQUE INDEX uq_billings_contract_competencia_recorrente ON billings(contract_id, competencia) WHERE is_deleted = false AND competencia_liberada = false AND billing_type IN ('recorrente','prorata','primeira_mensalidade','carne')"))
    connection.execute(text("CREATE UNIQUE INDEX uq_billings_item_parcela_efetiva ON billings(item_id, installment_number) WHERE is_deleted = false AND (status <> 'CANCELED' OR substituted_by_id IS NOT NULL)"))
    db.commit()
    old = cobranca(db, contrato, status=BillingStatus.CANCELED, is_deleted=True,
        billing_type='recorrente', period_label='11/2099')
    current = cobranca(db, contrato, billing_type='recorrente', period_label='11/2099')
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/d2a7c9e41b60_exclusao_canceladas.py'
    spec = importlib.util.spec_from_file_location('deletion_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.op = Operations(MigrationContext.configure(db.connection()))
    migration.upgrade()
    db.flush()
    db.refresh(old)
    assert old.competencia_liberada and current.amount == 100
    current.status = BillingStatus.CANCELED
    current.is_deleted = True
    db.flush()
    with pytest.raises(RuntimeError, match='reservadas'):
        migration.downgrade()
    current.competencia_liberada = True
    db.flush()
    migration.downgrade()
    assert db.scalar(text("SELECT count(*) FROM sqlite_master WHERE type='index' AND name='uq_billings_contract_competencia_recorrente'")) == 1
