"""Boletos pela API de Cobrança Sicredi.

Três tabelas novas, sem tocar em dados existentes:

* ``sicredi_boletos`` — título Sicredi 1:1 com a cobrança (estado do registro,
  dados oficiais, conciliação, baixa e pendência);
* ``sicredi_nosso_numero_sequencias`` — sequencial do nosso número gerado pelo
  beneficiário, alocado com trava de linha;
* ``sicredi_api_logs`` — log mascarado das chamadas à API.

Revision ID: e3b9d5a71c42
Revises: d6a4e9c27b10
"""
from alembic import op
import sqlalchemy as sa

revision = 'e3b9d5a71c42'
down_revision = 'd6a4e9c27b10'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'sicredi_boletos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('billing_id', sa.Integer(), sa.ForeignKey('billings.id'), nullable=False),
        sa.Column('ambiente', sa.String(10), nullable=False),
        sa.Column('cooperativa', sa.String(4), nullable=False),
        sa.Column('posto', sa.String(2), nullable=False),
        sa.Column('codigo_beneficiario', sa.String(5), nullable=False),
        sa.Column('nosso_numero', sa.String(9), nullable=False),
        sa.Column('seu_numero', sa.String(10), nullable=True),
        sa.Column('id_titulo_empresa', sa.String(25), nullable=True),
        sa.Column('tipo_cobranca', sa.String(10), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('situacao', sa.String(40), nullable=True),
        sa.Column('linha_digitavel', sa.String(60), nullable=True),
        sa.Column('codigo_barras', sa.String(60), nullable=True),
        sa.Column('txid', sa.String(60), nullable=True),
        sa.Column('qr_code', sa.Text(), nullable=True),
        sa.Column('valor_nominal', sa.Numeric(10, 2), nullable=True),
        sa.Column('data_vencimento', sa.Date(), nullable=True),
        sa.Column('payload_request', sa.JSON(), nullable=True),
        sa.Column('payload_response', sa.JSON(), nullable=True),
        sa.Column('erro', sa.Text(), nullable=True),
        sa.Column('registro_iniciado_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ultima_consulta_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ultima_consulta_erro', sa.Text(), nullable=True),
        sa.Column('falhas_consulta', sa.Integer(), server_default='0', nullable=False),
        sa.Column('data_liquidacao', sa.Date(), nullable=True),
        sa.Column('valor_liquidado', sa.Numeric(10, 2), nullable=True),
        sa.Column('baixa_status', sa.String(20), nullable=True),
        sa.Column('baixa_solicitada_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('baixa_enviada_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('baixa_transaction_id', sa.String(60), nullable=True),
        sa.Column('baixa_confirmada_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'baixa_confirmada_por_user_id', sa.Integer(),
            sa.ForeignKey('users.id', name='fk_sicredi_boletos_baixa_user'), nullable=True,
        ),
        sa.Column('baixa_observacao', sa.Text(), nullable=True),
        sa.Column('pendencia', sa.String(40), nullable=True),
        sa.Column('pendencia_detalhe', sa.JSON(), nullable=True),
        sa.Column('pendencia_desde', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('REGISTRANDO', 'DESFECHO_DESCONHECIDO', 'ERRO_REGISTRO', 'REGISTRADO')",
            name='ck_sicredi_boletos_status',
        ),
        sa.CheckConstraint(
            "baixa_status IS NULL OR baixa_status IN ('pendente', 'solicitada', 'confirmada')",
            name='ck_sicredi_boletos_baixa_status',
        ),
        sa.UniqueConstraint(
            'ambiente', 'cooperativa', 'posto', 'codigo_beneficiario', 'nosso_numero',
            name='uq_sicredi_boletos_nosso_numero',
        ),
    )
    op.create_index('ix_sicredi_boletos_billing_id', 'sicredi_boletos', ['billing_id'], unique=True)
    op.create_index('ix_sicredi_boletos_ultima_consulta_em', 'sicredi_boletos', ['ultima_consulta_em'])
    op.create_index('ix_sicredi_boletos_baixa_status', 'sicredi_boletos', ['baixa_status'])
    op.create_index('ix_sicredi_boletos_pendencia', 'sicredi_boletos', ['pendencia'])

    op.create_table(
        'sicredi_nosso_numero_sequencias',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ambiente', sa.String(10), nullable=False),
        sa.Column('cooperativa', sa.String(4), nullable=False),
        sa.Column('posto', sa.String(2), nullable=False),
        sa.Column('codigo_beneficiario', sa.String(5), nullable=False),
        sa.Column('ano', sa.Integer(), nullable=False),
        sa.Column('byte', sa.Integer(), nullable=False),
        sa.Column('ultimo', sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            'ambiente', 'cooperativa', 'posto', 'codigo_beneficiario', 'ano', 'byte',
            name='uq_sicredi_nosso_numero_sequencias',
        ),
    )

    op.create_table(
        'sicredi_api_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ambiente', sa.String(10), nullable=False),
        sa.Column('endpoint', sa.String(255), nullable=False),
        sa.Column('method', sa.String(10), nullable=False),
        sa.Column('request_payload', sa.JSON(), nullable=True),
        sa.Column('response_payload', sa.JSON(), nullable=True),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('billing_id', sa.Integer(), sa.ForeignKey('billings.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table('sicredi_api_logs')
    op.drop_table('sicredi_nosso_numero_sequencias')
    op.drop_index('ix_sicredi_boletos_pendencia', table_name='sicredi_boletos')
    op.drop_index('ix_sicredi_boletos_baixa_status', table_name='sicredi_boletos')
    op.drop_index('ix_sicredi_boletos_ultima_consulta_em', table_name='sicredi_boletos')
    op.drop_index('ix_sicredi_boletos_billing_id', table_name='sicredi_boletos')
    op.drop_table('sicredi_boletos')
