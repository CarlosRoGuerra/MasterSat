"""Fila persistente de fechamento e quota SMTP compartilhada."""
from alembic import op
import sqlalchemy as sa

revision = 'e7b5f0a32981'
down_revision = 'd6a4e9f21870'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'smtp_budgets',
        sa.Column('account', sa.String(64), primary_key=True),
        sa.Column('next_allowed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempts', sa.JSON(), nullable=False),
    )
    op.add_column('closure_email_deliveries', sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_closure_delivery_queue', 'closure_email_deliveries', ['status', 'id'])


def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT COUNT(*) FROM closure_email_deliveries WHERE status = 'aguardando'"
    )).scalar():
        raise RuntimeError('Aguarde o término da fila de e-mails antes do downgrade.')
    op.drop_index('ix_closure_delivery_queue', table_name='closure_email_deliveries')
    op.drop_column('closure_email_deliveries', 'next_attempt_at')
    op.drop_table('smtp_budgets')
