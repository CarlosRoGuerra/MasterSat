"""Cobrança só no sistema (carnê simples): não vai ao banco.

Marca as parcelas do carnê gerado sem a Ailos. A política de título bancário
recusa emitir essas cobranças na Ailos ou em remessa CNAB.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b8e3f1c6a274'
down_revision = 'a7d2c4e9f150'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'billings',
        sa.Column('somente_sistema', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column('billings', 'somente_sistema')
