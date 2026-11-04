"""Fila persistente para lotes e carnês Sicredi.

Revision ID: f4c8b2a63d10
Revises: e3b9d5a71c42
"""
from alembic import op
import sqlalchemy as sa

revision = 'f4c8b2a63d10'
down_revision = 'e3b9d5a71c42'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'sicredi_lotes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('chave', sa.String(64), nullable=False, unique=True),
        sa.Column('ticket', sa.String(36), nullable=False),
        sa.Column('tipo', sa.String(10), nullable=False),
        sa.Column('ambiente', sa.String(10), nullable=False),
        sa.Column('cooperativa', sa.String(4), nullable=False),
        sa.Column('posto', sa.String(2), nullable=False),
        sa.Column('codigo_beneficiario', sa.String(5), nullable=False),
        sa.Column('payer_client_id', sa.Integer(), sa.ForeignKey('clients.id'), nullable=True),
        sa.Column('billing_ids', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('erros', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_sicredi_lotes_ticket', 'sicredi_lotes', ['ticket'], unique=True)
    op.create_index('ix_sicredi_lotes_payer_client_id', 'sicredi_lotes', ['payer_client_id'])
    op.create_index('ix_sicredi_lotes_status', 'sicredi_lotes', ['status'])
    op.add_column('sicredi_boletos', sa.Column('lote_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_sicredi_boletos_lote_id', 'sicredi_boletos', 'sicredi_lotes', ['lote_id'], ['id'])
    op.create_index('ix_sicredi_boletos_lote_id', 'sicredi_boletos', ['lote_id'])
    op.drop_constraint('ck_sicredi_boletos_status', 'sicredi_boletos', type_='check')
    op.create_check_constraint('ck_sicredi_boletos_status', 'sicredi_boletos',
                              "status IN ('NA_FILA', 'REGISTRANDO', 'DESFECHO_DESCONHECIDO', 'ERRO_REGISTRO', 'REGISTRADO')")


def downgrade():
    # Uma reserva ainda não enviada deixa de ocupar a fila ao voltar de versão.
    op.execute("UPDATE sicredi_boletos SET status = 'ERRO_REGISTRO' WHERE status = 'NA_FILA'")
    op.drop_constraint('ck_sicredi_boletos_status', 'sicredi_boletos', type_='check')
    op.create_check_constraint('ck_sicredi_boletos_status', 'sicredi_boletos',
                              "status IN ('REGISTRANDO', 'DESFECHO_DESCONHECIDO', 'ERRO_REGISTRO', 'REGISTRADO')")
    op.drop_index('ix_sicredi_boletos_lote_id', table_name='sicredi_boletos')
    op.drop_constraint('fk_sicredi_boletos_lote_id', 'sicredi_boletos', type_='foreignkey')
    op.drop_column('sicredi_boletos', 'lote_id')
    op.drop_table('sicredi_lotes')
