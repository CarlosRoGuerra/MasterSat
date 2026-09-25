"""billings.sgr_payload: dados do boleto de origem no SGR

Guarda o registro do boleto como o SGR devolve (cod_boleto, tipo_boleto,
nosso_numero, data_vencimento_original, data_credito_banco, numero_nf,
matriz_filial, placas, discriminação...). Esses campos não têm coluna
própria em `billings` e são o que a tela de detalhes precisa mostrar para
uma cobrança migrada — o boleto bancário em si não existe mais do lado do
SGR depois de baixado.

Mesmo padrão já usado em ailos_boletos.payload_request/payload_response:
payload da origem em JSON, sem inventar uma coluna por campo de terceiro.

Revision ID: c7b2f9a41d38
Revises: d4a8b1e6c2f9
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c7b2f9a41d38'
down_revision: Union[str, None] = 'd4a8b1e6c2f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('billings', sa.Column('sgr_payload', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('billings', 'sgr_payload')
