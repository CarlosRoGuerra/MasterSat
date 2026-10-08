"""Identificador completo para equipamento sem placa oficial.

Revision ID: c8f2a6d41e90
Revises: b8e3f1c6a274
"""
from alembic import op
import sqlalchemy as sa

revision = 'c8f2a6d41e90'
down_revision = 'b8e3f1c6a274'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('vehicles', sa.Column('is_non_road_asset', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column('vehicles', 'plate', existing_type=sa.String(10), type_=sa.String(40), existing_nullable=False)


def downgrade():
    # Não trunca identificadores de equipamentos ao tentar voltar a versão.
    connection = op.get_bind()
    if connection.execute(sa.text('SELECT 1 FROM vehicles WHERE length(plate) > 10 OR is_non_road_asset LIMIT 1')).first():
        raise RuntimeError('Existem equipamentos sem placa; downgrade não pode descartar seus identificadores ou classificação.')
    op.alter_column('vehicles', 'plate', existing_type=sa.String(40), type_=sa.String(10), existing_nullable=False)
    op.drop_column('vehicles', 'is_non_road_asset')
