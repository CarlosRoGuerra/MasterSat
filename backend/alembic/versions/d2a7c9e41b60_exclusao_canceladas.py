"""Preserve the month of deleted canceled bills until it is safely released.

Revision ID: d2a7c9e41b60
Revises: c1f6a8d3e902
"""
from alembic import op
import sqlalchemy as sa

revision = 'd2a7c9e41b60'
down_revision = 'c1f6a8d3e902'
branch_labels = None
depends_on = None

_TYPES = "billing_type IN ('recorrente', 'prorata', 'primeira_mensalidade', 'carne')"
_OLD = f'is_deleted = false AND competencia_liberada = false AND {_TYPES}'
_NEW = f"(is_deleted = false OR status = 'CANCELED') AND competencia_liberada = false AND {_TYPES}"
_INDEX = 'uq_billings_contract_competencia_recorrente'
_ITEM_INDEX = 'uq_billings_item_parcela_efetiva'
_ITEM_OLD = "is_deleted = false AND (status <> 'CANCELED' OR substituted_by_id IS NOT NULL)"
_ITEM_NEW = "(is_deleted = false OR (status = 'CANCELED' AND competencia_liberada = false)) AND (status <> 'CANCELED' OR substituted_by_id IS NOT NULL)"


def _index(predicate, item_predicate):
    op.create_index(_INDEX, 'billings', ['contract_id', 'competencia'], unique=True,
        postgresql_where=sa.text(predicate), sqlite_where=sa.text(predicate))
    op.create_index(_ITEM_INDEX, 'billings', ['item_id', 'installment_number'], unique=True,
        postgresql_where=sa.text(item_predicate), sqlite_where=sa.text(item_predicate))


def upgrade():
    # As exclusões anteriores já liberavam o mês. Preservar esse comportamento
    # evita reocupar competências antigas, inclusive as já cobradas novamente.
    op.execute(sa.text(f"UPDATE billings SET competencia_liberada = true "
        "WHERE is_deleted = true AND status = 'CANCELED' AND competencia_liberada = false"))
    op.drop_index(_INDEX, table_name='billings')
    op.drop_index(_ITEM_INDEX, table_name='billings')
    _index(_NEW, _ITEM_NEW)


def downgrade():
    reserved = op.get_bind().execute(sa.text(f"SELECT count(*) FROM billings WHERE "
        f"is_deleted = true AND status = 'CANCELED' AND competencia_liberada = false "
        f"AND ((contract_id IS NOT NULL AND competencia IS NOT NULL AND {_TYPES}) "
        "OR (item_id IS NOT NULL AND substituted_by_id IS NOT NULL))")).scalar_one()
    if reserved:
        raise RuntimeError('Há competências reservadas por cobranças excluídas. Resolva a baixa e libere essas competências antes do downgrade.')
    op.drop_index(_INDEX, table_name='billings')
    op.drop_index(_ITEM_INDEX, table_name='billings')
    _index(_OLD, _ITEM_OLD)
