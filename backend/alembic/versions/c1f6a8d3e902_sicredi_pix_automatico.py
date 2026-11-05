"""Add Pix Automatico as a payment method and its durable lifecycle records.

Revision ID: c1f6a8d3e902
Revises: b8e4c6d2a907
"""
from alembic import op
import sqlalchemy as sa

revision = 'c1f6a8d3e902'
down_revision = 'b8e4c6d2a907'
branch_labels = None
depends_on = None

_OLD = "forma_cobranca IS NULL OR forma_cobranca IN ('boleto_mensal', 'carne_ailos', 'carne_sicredi', 'carne_simples', 'cartao_credito')"
_NEW = _OLD[:-1] + ", 'pix_automatico')"


def _timestamps():
    return [sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade():
    with op.batch_alter_table('clients') as batch:
        batch.drop_constraint('ck_clients_forma_cobranca', type_='check')
        batch.create_check_constraint('ck_clients_forma_cobranca', _NEW)
    op.create_table('sicredi_pix_recorrencias',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id'), nullable=False),
        sa.Column('ambiente', sa.String(20), nullable=False),
        sa.Column('referencia', sa.String(35), unique=True, nullable=False),
        sa.Column('id_rec', sa.String(29), unique=True),
        sa.Column('location_id', sa.String(30)),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('ativa', sa.Boolean, nullable=False),
        sa.Column('data_inicial', sa.Date, nullable=False),
        sa.Column('objeto', sa.String(35), nullable=False),
        sa.Column('valor_minimo', sa.Numeric(10, 2), nullable=False),
        sa.Column('politica_retentativa', sa.String(20), nullable=False),
        sa.Column('pix_copia_cola', sa.Text),
        sa.Column('erro', sa.Text),
        sa.Column('consultada_em', sa.DateTime(timezone=True)),
        sa.Column('processar_evento', sa.Boolean, nullable=False), *_timestamps())
    op.create_index('ix_sicredi_pix_recorrencias_client_id', 'sicredi_pix_recorrencias', ['client_id'])
    op.create_index('uq_sicredi_pix_recorrencia_ativa', 'sicredi_pix_recorrencias', ['client_id', 'ambiente'], unique=True,
                    postgresql_where=sa.text('ativa = true'), sqlite_where=sa.text('ativa = 1'))
    op.create_table('sicredi_pix_cobrancas',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('billing_id', sa.Integer, sa.ForeignKey('billings.id'), unique=True, nullable=False),
        sa.Column('recorrencia_id', sa.Integer, sa.ForeignKey('sicredi_pix_recorrencias.id'), nullable=False),
        sa.Column('txid', sa.String(35), unique=True, nullable=False),
        sa.Column('ciclo', sa.Date, nullable=False),
        sa.Column('vencimento', sa.Date, nullable=False),
        sa.Column('valor', sa.Numeric(10, 2), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('enviada_em', sa.DateTime(timezone=True)),
        sa.Column('consultada_em', sa.DateTime(timezone=True)),
        sa.Column('proxima_tentativa', sa.DateTime(timezone=True)),
        sa.Column('tentativas', sa.Integer, nullable=False),
        sa.Column('erro', sa.Text),
        sa.Column('pendencia', sa.Text),
        sa.Column('processar_evento', sa.Boolean, nullable=False), *_timestamps())
    op.create_index('ix_sicredi_pix_cobrancas_recorrencia_id', 'sicredi_pix_cobrancas', ['recorrencia_id'])
    op.create_index('ix_sicredi_pix_cobrancas_status', 'sicredi_pix_cobrancas', ['status'])
    op.create_index('uq_sicredi_pix_cobranca_ciclo', 'sicredi_pix_cobrancas', ['recorrencia_id', 'ciclo'], unique=True)
    op.create_table('sicredi_pix_recebimentos',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('cobranca_id', sa.Integer, sa.ForeignKey('sicredi_pix_cobrancas.id'), nullable=False),
        sa.Column('end_to_end_id', sa.String(32), unique=True, nullable=False),
        sa.Column('valor', sa.Numeric(10, 2), nullable=False),
        sa.Column('horario', sa.DateTime(timezone=True), nullable=False),
        sa.Column('aplicado', sa.Boolean, nullable=False),
        sa.Column('pendencia', sa.Text), *_timestamps())
    op.create_index('ix_sicredi_pix_recebimentos_cobranca_id', 'sicredi_pix_recebimentos', ['cobranca_id'])


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT count(*) FROM clients WHERE forma_cobranca = 'pix_automatico'")).scalar():
        raise RuntimeError('Existem clientes com Pix Automático. Migre a forma de cobrança antes de reverter.')
    for table in ('sicredi_pix_recebimentos', 'sicredi_pix_cobrancas', 'sicredi_pix_recorrencias'):
        if connection.execute(sa.text(f'SELECT count(*) FROM {table}')).scalar():
            raise RuntimeError('Há histórico Pix. A reversão removeria dados financeiros e foi recusada.')
    op.drop_table('sicredi_pix_recebimentos')
    op.drop_table('sicredi_pix_cobrancas')
    op.drop_table('sicredi_pix_recorrencias')
    with op.batch_alter_table('clients') as batch:
        batch.drop_constraint('ck_clients_forma_cobranca', type_='check')
        batch.create_check_constraint('ck_clients_forma_cobranca', _OLD)
