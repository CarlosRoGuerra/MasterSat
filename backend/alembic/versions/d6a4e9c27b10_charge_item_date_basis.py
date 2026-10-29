"""Distingue data do serviço e vencimento no lançamento adicional.

Revision ID: d6a4e9c27b10
Revises: c8f2a6d41e90
"""
from alembic import op
import sqlalchemy as sa

revision = 'd6a4e9c27b10'
down_revision = 'c8f2a6d41e90'
branch_labels = None
depends_on = None


def upgrade():
    # Os dados existentes conservam a interpretação anterior. Correções de
    # vencimento são explícitas e limitadas ao lançamento selecionado.
    op.add_column('client_charge_items', sa.Column('date_basis', sa.String(20),
        server_default='service_date', nullable=False))
    op.create_check_constraint('ck_client_charge_items_date_basis', 'client_charge_items',
        "date_basis IN ('service_date', 'due_date')")


def downgrade():
    op.drop_constraint('ck_client_charge_items_date_basis', 'client_charge_items', type_='check')
    op.drop_column('client_charge_items', 'date_basis')
