"""Fase 01: consumo único de refresh/reset e prazo do state Ailos

Três mudanças aditivas, todas para tornar explícito o consumo de credenciais
de uso único:

* ``refresh_tokens.rotated_at`` — instante em que o token foi trocado por um
  sucessor. Distingue duas abas renovando ao mesmo tempo (disputa legítima,
  segundos) de um token antigo reapresentado depois (reuso → revoga a família).
* ``password_reset_tokens.token_hash`` — o token de redefinição passa a ser
  guardado só como SHA-256. Os tokens pendentes são convertidos (continuam
  válidos até expirar, no máximo PASSWORD_RESET_EXPIRE_MINUTES) e a coluna
  ``token`` em texto puro é esvaziada. ``sent_at``/``delivery_attempts``/
  ``delivery_error`` registram a entrega do e-mail.
* ``ailos_integrations.state_expires_at`` — prazo do ``state`` do fluxo de
  autorização do cooperado. States já gravados ficam sem prazo e são tratados
  como expirados pelo código novo (basta clicar em "Reconectar Ailos").

Nenhuma linha é apagada. O downgrade não recupera tokens em texto puro (não
existe mais cópia deles): os pendentes são marcados como usados.

Revision ID: d9e4f1a7b2c5
Revises: b3f8a1c9d2e7
"""
import hashlib
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd9e4f1a7b2c5'
down_revision: Union[str, None] = 'b3f8a1c9d2e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('refresh_tokens', sa.Column('rotated_at', sa.DateTime(timezone=True), nullable=True))

    op.add_column('password_reset_tokens', sa.Column('token_hash', sa.String(length=64), nullable=True))
    op.add_column('password_reset_tokens', sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'password_reset_tokens',
        sa.Column('delivery_attempts', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column('password_reset_tokens', sa.Column('delivery_error', sa.String(length=255), nullable=True))
    op.alter_column('password_reset_tokens', 'token', existing_type=sa.String(length=120), nullable=True)

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        'SELECT id, token FROM password_reset_tokens WHERE token IS NOT NULL'
    )).fetchall()
    for row_id, token in rows:
        bind.execute(
            sa.text('UPDATE password_reset_tokens SET token_hash = :h, token = NULL WHERE id = :id'),
            {'h': hashlib.sha256(token.encode('utf-8')).hexdigest(), 'id': row_id},
        )

    op.create_index(
        op.f('ix_password_reset_tokens_token_hash'), 'password_reset_tokens', ['token_hash'], unique=True,
    )

    op.add_column('ailos_integrations', sa.Column('state_expires_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('ailos_integrations', 'state_expires_at')

    # Sem o texto puro, o código anterior não consegue validar nenhum token
    # pendente: marca como usado e preenche a coluna NOT NULL com um valor
    # que nunca coincide com um token emitido (uuid4().hex não tem ':').
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE password_reset_tokens "
        "SET used_at = COALESCE(used_at, CURRENT_TIMESTAMP), token = 'revogado:' || token_hash "
        "WHERE token IS NULL AND token_hash IS NOT NULL"
    ))
    bind.execute(sa.text(
        "UPDATE password_reset_tokens SET used_at = COALESCE(used_at, CURRENT_TIMESTAMP), "
        "token = 'revogado:id' || CAST(id AS VARCHAR) WHERE token IS NULL"
    ))
    op.drop_index(op.f('ix_password_reset_tokens_token_hash'), table_name='password_reset_tokens')
    op.alter_column('password_reset_tokens', 'token', existing_type=sa.String(length=120), nullable=False)
    op.drop_column('password_reset_tokens', 'delivery_error')
    op.drop_column('password_reset_tokens', 'delivery_attempts')
    op.drop_column('password_reset_tokens', 'sent_at')
    op.drop_column('password_reset_tokens', 'token_hash')

    op.drop_column('refresh_tokens', 'rotated_at')
