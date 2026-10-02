"""Classify existing closure titles as consolidated billings.

Revision ID: d6c7e8f90123
Revises: c49d8a6e1b20
"""
from alembic import op
import sqlalchemy as sa


revision = 'd6c7e8f90123'
down_revision = 'c49d8a6e1b20'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE billings AS parent
        SET billing_type = 'boleto_unico'
        WHERE parent.billing_type = 'avulsa'
          AND parent.title LIKE 'Fechamento % - boleto único'
          AND EXISTS (
              SELECT 1 FROM billings AS child
              WHERE child.substituted_by_id = parent.id
                AND child.is_deleted = false
          )
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        UPDATE billings
        SET billing_type = 'avulsa'
        WHERE billing_type = 'boleto_unico'
    """))
