"""Completa vínculo de lotes em bancos com revisão marcada e schema incompleto.

Revision ID: b8e4c6d2a907
Revises: a6e2d9c51b74
"""
from alembic import op
import sqlalchemy as sa

revision = 'b8e4c6d2a907'
down_revision = 'a6e2d9c51b74'
branch_labels = None
depends_on = None


def upgrade():
    # f4c8b2a63d10 já define estes objetos. A correção só completa o que
    # estiver ausente; bancos que receberam a migration inteira não mudam.
    inspector = sa.inspect(op.get_bind())
    if 'lote_id' not in {c['name'] for c in inspector.get_columns('sicredi_boletos')}:
        op.add_column('sicredi_boletos', sa.Column('lote_id', sa.Integer(), nullable=True))
    if not any(fk['constrained_columns'] == ['lote_id']
               for fk in inspector.get_foreign_keys('sicredi_boletos')):
        op.create_foreign_key('fk_sicredi_boletos_lote_id', 'sicredi_boletos',
                              'sicredi_lotes', ['lote_id'], ['id'])
    if 'ix_sicredi_boletos_lote_id' not in {i['name'] for i in inspector.get_indexes('sicredi_boletos')}:
        op.create_index('ix_sicredi_boletos_lote_id', 'sicredi_boletos', ['lote_id'])
    check = next((c for c in inspector.get_check_constraints('sicredi_boletos')
                  if c['name'] == 'ck_sicredi_boletos_status'), None)
    if check is None or 'NA_FILA' not in check['sqltext']:
        if check is not None:
            op.drop_constraint('ck_sicredi_boletos_status', 'sicredi_boletos', type_='check')
        op.create_check_constraint('ck_sicredi_boletos_status', 'sicredi_boletos',
                                  "status IN ('NA_FILA', 'REGISTRANDO', 'DESFECHO_DESCONHECIDO', 'ERRO_REGISTRO', 'REGISTRADO')")


def downgrade():
    # Estes objetos pertencem a f4c8b2a63d10 e devem continuar presentes ao
    # voltar para a6e2d9c51b74. O downgrade de f4 os remove normalmente.
    pass
