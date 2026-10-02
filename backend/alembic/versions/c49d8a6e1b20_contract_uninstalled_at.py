"""Keep the uninstall date on contracts for the final monthly charge.

Revision ID: c49d8a6e1b20
Revises: e929b0a7e7f3
"""
from alembic import op
import sqlalchemy as sa


revision = 'c49d8a6e1b20'
down_revision = 'e929b0a7e7f3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('contracts', sa.Column('uninstalled_at', sa.Date(), nullable=True))
    op.create_index('ix_contracts_uninstalled_at', 'contracts', ['uninstalled_at'])
    # The uninstall endpoint already recorded both the end date and this note.
    # Ordinary cancellations have no marker and must not create a final charge.
    op.execute(sa.text("""
        UPDATE contracts
        SET uninstalled_at = end_date
        WHERE end_date IS NOT NULL
          AND status = 'cancelado'
          AND notes LIKE '%Desinstalado em %'
    """))


def downgrade() -> None:
    op.drop_index('ix_contracts_uninstalled_at', table_name='contracts')
    op.drop_column('contracts', 'uninstalled_at')
