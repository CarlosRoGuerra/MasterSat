"""Forma de cobrança Carnê Sicredi.

Revision ID: a6e2d9c51b74
Revises: f4c8b2a63d10
"""
from alembic import op

revision = 'a6e2d9c51b74'
down_revision = 'f4c8b2a63d10'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('ck_clients_forma_cobranca', 'clients', type_='check')
    op.create_check_constraint('ck_clients_forma_cobranca', 'clients',
                              "forma_cobranca IS NULL OR forma_cobranca IN ('boleto_mensal', 'carne_ailos', 'carne_sicredi', 'carne_simples', 'cartao_credito')")


def downgrade():
    # Não inventa outro banco para os cadastros ao voltar uma versão.
    op.execute("UPDATE clients SET forma_cobranca = NULL WHERE forma_cobranca = 'carne_sicredi'")
    op.drop_constraint('ck_clients_forma_cobranca', 'clients', type_='check')
    op.create_check_constraint('ck_clients_forma_cobranca', 'clients',
                              "forma_cobranca IS NULL OR forma_cobranca IN ('boleto_mensal', 'carne_ailos', 'carne_simples', 'cartao_credito')")
