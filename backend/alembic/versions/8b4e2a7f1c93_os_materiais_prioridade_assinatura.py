"""os materiais prioridade assinatura

Módulo de Ordem de Serviço profissional: prioridade, descrição do problema/
execução separadas, materiais utilizados, assinatura digital (técnico e
cliente) e versionamento de documento gerado (compartilhado com client/
vehicle via Document, mas só populado pelo fluxo de OS nesta migration).

Escrita à mão (não via `alembic revision --autogenerate`): o autogenerate
contra o banco de dev local também detectou drift pré-existente e não
relacionado (contracts.delivery_method, ailos_integrations.auto_relogin_failures
etc.) que não faz parte desta mudança — essa migration cobre só o escopo do
módulo de OS.

Revision ID: 8b4e2a7f1c93
Revises: f7c1a2b3d4e5
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '8b4e2a7f1c93'
down_revision: Union[str, None] = 'f7c1a2b3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ORDER_PRIORITY = postgresql.ENUM('LOW', 'NORMAL', 'HIGH', 'URGENT', name='orderpriority')


def upgrade() -> None:
    # ── service_orders: prioridade, descrições separadas, assinatura ────
    _ORDER_PRIORITY.create(op.get_bind(), checkfirst=True)
    op.add_column('service_orders', sa.Column(
        'priority', _ORDER_PRIORITY, nullable=False, server_default='NORMAL',
    ))
    op.add_column('service_orders', sa.Column('problem_description', sa.Text(), nullable=True))
    op.add_column('service_orders', sa.Column('execution_description', sa.Text(), nullable=True))
    op.add_column('service_orders', sa.Column('technician_signature_document_id', sa.Integer(), nullable=True))
    op.add_column('service_orders', sa.Column('technician_signed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('service_orders', sa.Column('client_signature_document_id', sa.Integer(), nullable=True))
    op.add_column('service_orders', sa.Column('client_signed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        'service_orders_technician_signature_document_id_fkey',
        'service_orders', 'documents', ['technician_signature_document_id'], ['id'],
    )
    op.create_foreign_key(
        'service_orders_client_signature_document_id_fkey',
        'service_orders', 'documents', ['client_signature_document_id'], ['id'],
    )

    # ── documents: versionamento (compartilhado, aditivo — client/vehicle
    #    ficam sempre version=1/supersedes=NULL, só a OS popula de fato) ──
    op.add_column('documents', sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('documents', sa.Column('supersedes_document_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'documents_supersedes_document_id_fkey',
        'documents', 'documents', ['supersedes_document_id'], ['id'],
    )

    # ── service_order_materials (tabela nova) ────────────────────────────
    op.create_table(
        'service_order_materials',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('service_order_id', sa.Integer(), nullable=False),
        sa.Column('service_product_id', sa.Integer(), nullable=True),
        sa.Column('description', sa.String(length=300), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('unit', sa.String(length=10), nullable=True),
        sa.Column('unit_price', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.ForeignKeyConstraint(['service_order_id'], ['service_orders.id'], ),
        sa.ForeignKeyConstraint(['service_product_id'], ['service_products.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_service_order_materials_id'), 'service_order_materials', ['id'], unique=False)
    op.create_index(op.f('ix_service_order_materials_service_order_id'), 'service_order_materials', ['service_order_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_service_order_materials_service_order_id'), table_name='service_order_materials')
    op.drop_index(op.f('ix_service_order_materials_id'), table_name='service_order_materials')
    op.drop_table('service_order_materials')

    op.drop_constraint('documents_supersedes_document_id_fkey', 'documents', type_='foreignkey')
    op.drop_column('documents', 'supersedes_document_id')
    op.drop_column('documents', 'version')

    op.drop_constraint('service_orders_client_signature_document_id_fkey', 'service_orders', type_='foreignkey')
    op.drop_constraint('service_orders_technician_signature_document_id_fkey', 'service_orders', type_='foreignkey')
    op.drop_column('service_orders', 'client_signed_at')
    op.drop_column('service_orders', 'client_signature_document_id')
    op.drop_column('service_orders', 'technician_signed_at')
    op.drop_column('service_orders', 'technician_signature_document_id')
    op.drop_column('service_orders', 'execution_description')
    op.drop_column('service_orders', 'problem_description')
    op.drop_column('service_orders', 'priority')
    _ORDER_PRIORITY.drop(op.get_bind(), checkfirst=True)
