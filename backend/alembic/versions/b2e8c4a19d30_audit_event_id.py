"""Idempotency key for queued audit events.

Revision ID: b2e8c4a19d30
Revises: a4c7e2f9b1d6
"""

from alembic import op
import sqlalchemy as sa

revision = 'b2e8c4a19d30'
down_revision = 'a4c7e2f9b1d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('audit_logs', sa.Column('event_id', sa.String(length=36), nullable=True))
    op.create_index(
        'uq_audit_logs_event_id', 'audit_logs', ['event_id'], unique=True,
        postgresql_where=sa.text('event_id IS NOT NULL'),
        sqlite_where=sa.text('event_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_audit_logs_event_id', table_name='audit_logs')
    op.drop_column('audit_logs', 'event_id')
