"""Forma de cobrança do cliente (boleto mensal, carnê Ailos, carnê simples, cartão).

Filtro do relatório de cobranças e do fechamento. Nulo = não informado.
"""
from alembic import op
import sqlalchemy as sa

revision = 'a7d2c4e9f150'
down_revision = 'f3c8a1d6b204'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('clients', sa.Column('forma_cobranca', sa.String(20), nullable=True))
    op.create_index('ix_clients_forma_cobranca', 'clients', ['forma_cobranca'])
    op.create_check_constraint(
        'ck_clients_forma_cobranca', 'clients',
        "forma_cobranca IS NULL OR forma_cobranca IN "
        "('boleto_mensal', 'carne_ailos', 'carne_simples', 'cartao_credito')",
    )


def downgrade():
    op.drop_constraint('ck_clients_forma_cobranca', 'clients', type_='check')
    op.drop_index('ix_clients_forma_cobranca', table_name='clients')
    op.drop_column('clients', 'forma_cobranca')
