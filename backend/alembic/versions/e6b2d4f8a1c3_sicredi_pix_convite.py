"""Register the Pix Automatico invitation sent to the payer.

Additive only: nullable columns (and a counter with server default 0) on
sicredi_pix_recorrencias. Existing authorizations keep every value; the
downgrade only drops these columns.

Revision ID: e6b2d4f8a1c3
Revises: d2a7c9e41b60
"""
from alembic import op
import sqlalchemy as sa

revision = 'e6b2d4f8a1c3'
down_revision = 'd2a7c9e41b60'
branch_labels = None
depends_on = None

_COLUMNS = (
    ('convite_canal', sa.String(20)),
    ('convite_status', sa.String(20)),
    ('convite_destino', sa.Text()),
    ('convite_iniciado_em', sa.DateTime(timezone=True)),
    ('convite_enviado_em', sa.DateTime(timezone=True)),
    ('convite_erro', sa.Text()),
)


def upgrade():
    with op.batch_alter_table('sicredi_pix_recorrencias') as batch:
        for name, type_ in _COLUMNS:
            batch.add_column(sa.Column(name, type_, nullable=True))
        batch.add_column(sa.Column('convite_tentativas', sa.Integer(), nullable=False, server_default=sa.text('0')))


def downgrade():
    with op.batch_alter_table('sicredi_pix_recorrencias') as batch:
        batch.drop_column('convite_tentativas')
        for name, _ in reversed(_COLUMNS):
            batch.drop_column(name)
