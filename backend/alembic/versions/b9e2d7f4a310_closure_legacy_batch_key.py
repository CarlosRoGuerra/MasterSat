"""Identify legacy closure batches by the transaction timestamp of their titles.

Revision ID: b9e2d7f4a310
Revises: a8f3c6d1e209
"""
from alembic import op
import sqlalchemy as sa


revision = 'b9e2d7f4a310'
down_revision = 'a8f3c6d1e209'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('closure_jobs', sa.Column('legacy_created_at', sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint('uq_closure_jobs_legacy_created_at', 'closure_jobs', ['legacy_created_at'])


def downgrade() -> None:
    op.drop_constraint('uq_closure_jobs_legacy_created_at', 'closure_jobs', type_='unique')
    op.drop_column('closure_jobs', 'legacy_created_at')
