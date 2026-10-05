"""Store every recipient of a closure delivery without truncation."""
from alembic import op
import sqlalchemy as sa

revision = 'd6a4e9f21870'
down_revision = 'b9e2d7f4a310'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('closure_email_deliveries') as batch_op:
        batch_op.alter_column('recipient', existing_type=sa.String(180), type_=sa.Text(), existing_nullable=True)


def downgrade() -> None:
    # Refuse a lossy downgrade when a delivery has more than 180 characters.
    if op.get_bind().execute(sa.text(
        'SELECT COUNT(*) FROM closure_email_deliveries WHERE length(recipient) > 180'
    )).scalar():
        raise RuntimeError('Há envios com múltiplos destinatários; o downgrade truncaria o histórico.')
    with op.batch_alter_table('closure_email_deliveries') as batch_op:
        batch_op.alter_column('recipient', existing_type=sa.Text(), type_=sa.String(180), existing_nullable=True)
