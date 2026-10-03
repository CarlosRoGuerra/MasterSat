"""Store delivery outcomes for closure batches.

Revision ID: a8f3c6d1e209
Revises: d6c7e8f90123
"""
from alembic import op
import sqlalchemy as sa


revision = 'a8f3c6d1e209'
down_revision = 'd6c7e8f90123'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'closure_email_deliveries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('closure_job_id', sa.Integer(), sa.ForeignKey('closure_jobs.id'), nullable=False),
        sa.Column('billing_id', sa.Integer(), sa.ForeignKey('billings.id'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('recipient', sa.String(180)),
        sa.Column('error', sa.Text()),
        sa.Column('started_at', sa.DateTime(timezone=True)),
        sa.Column('sent_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('closure_job_id', 'billing_id', name='uq_closure_email_delivery_title'),
    )
    op.create_index('ix_closure_email_deliveries_closure_job_id', 'closure_email_deliveries', ['closure_job_id'])
    op.create_index('ix_closure_email_deliveries_billing_id', 'closure_email_deliveries', ['billing_id'])


def downgrade() -> None:
    op.drop_index('ix_closure_email_deliveries_billing_id', table_name='closure_email_deliveries')
    op.drop_index('ix_closure_email_deliveries_closure_job_id', table_name='closure_email_deliveries')
    op.drop_table('closure_email_deliveries')
