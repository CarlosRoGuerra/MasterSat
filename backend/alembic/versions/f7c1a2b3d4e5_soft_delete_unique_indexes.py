"""align unique indexes with soft delete semantics

Revision ID: f7c1a2b3d4e5
Revises: e0905f77f744
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7c1a2b3d4e5'
down_revision: Union[str, None] = 'e0905f77f744'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _abort_if_duplicates(
    table_name: str,
    column_name: str,
    *,
    active_only: bool,
) -> None:
    where_parts = [f'{column_name} IS NOT NULL']
    if active_only:
        where_parts.append('is_deleted = false')
    where_sql = ' AND '.join(where_parts)
    scope = 'ativos' if active_only else 'incluindo removidos'

    op.execute(
        sa.text(
            f"""
            DO $migration$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM {table_name}
                    WHERE {where_sql}
                    GROUP BY {column_name}
                    HAVING COUNT(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'Migration f7c1a2b3d4e5: valores duplicados em {table_name}.{column_name} ({scope})';
                END IF;
            END
            $migration$;
            """
        )
    )


def _preflight_upgrade() -> None:
    _abort_if_duplicates('clients', 'cpf_cnpj', active_only=True)
    _abort_if_duplicates('vehicles', 'plate', active_only=True)
    _abort_if_duplicates('vehicles', 'chassis', active_only=True)
    _abort_if_duplicates('trackers', 'imei', active_only=True)

    op.execute(
        sa.text(
            """
            DO $migration$
            BEGIN
                IF EXISTS (SELECT 1 FROM users WHERE btrim(email) = '') THEN
                    RAISE EXCEPTION
                        'Migration f7c1a2b3d4e5: users.email vazio nao pode ser normalizado';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM users
                    GROUP BY lower(btrim(email))
                    HAVING COUNT(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'Migration f7c1a2b3d4e5: users.email possui duplicidades normalizadas';
                END IF;
            END
            $migration$;
            """
        )
    )


def _preflight_downgrade() -> None:
    # O schema antigo era globalmente unico. Depois que identificadores de
    # registros removidos forem reutilizados, voltar sem reconciliar os dados
    # perderia essa garantia; o downgrade deve falhar antes de remover indices.
    _abort_if_duplicates('clients', 'cpf_cnpj', active_only=False)
    _abort_if_duplicates('vehicles', 'plate', active_only=False)
    _abort_if_duplicates('vehicles', 'chassis', active_only=False)
    _abort_if_duplicates('trackers', 'imei', active_only=False)


def upgrade() -> None:
    _preflight_upgrade()

    # Canonicaliza o legado antes do indice funcional. Novas escritas ja sao
    # normalizadas pelos schemas de criacao e atualizacao.
    op.execute(sa.text('UPDATE users SET email = lower(btrim(email))'))

    op.drop_index('ix_clients_cpf_cnpj', table_name='clients')
    op.drop_index('ix_vehicles_plate', table_name='vehicles')
    op.drop_constraint('vehicles_chassis_key', 'vehicles', type_='unique')
    op.drop_index('ix_trackers_imei', table_name='trackers')
    op.drop_index('ix_users_email', table_name='users')

    op.create_index(
        'uq_clients_cpf_cnpj_active',
        'clients',
        ['cpf_cnpj'],
        unique=True,
        postgresql_where=sa.text('is_deleted = false'),
    )
    op.create_index(
        'uq_vehicles_plate_active',
        'vehicles',
        ['plate'],
        unique=True,
        postgresql_where=sa.text('is_deleted = false'),
    )
    op.create_index(
        'uq_vehicles_chassis_active',
        'vehicles',
        ['chassis'],
        unique=True,
        postgresql_where=sa.text('is_deleted = false'),
    )
    op.create_index(
        'uq_trackers_imei_active',
        'trackers',
        ['imei'],
        unique=True,
        postgresql_where=sa.text('is_deleted = false'),
    )
    op.create_index(
        'uq_users_email_lower',
        'users',
        [sa.text('lower(email)')],
        unique=True,
    )


def downgrade() -> None:
    _preflight_downgrade()

    op.drop_index('uq_users_email_lower', table_name='users')
    op.drop_index('uq_trackers_imei_active', table_name='trackers')
    op.drop_index('uq_vehicles_chassis_active', table_name='vehicles')
    op.drop_index('uq_vehicles_plate_active', table_name='vehicles')
    op.drop_index('uq_clients_cpf_cnpj_active', table_name='clients')

    op.create_index('ix_users_email', 'users', ['email'], unique=True)
    op.create_index('ix_trackers_imei', 'trackers', ['imei'], unique=True)
    op.create_unique_constraint('vehicles_chassis_key', 'vehicles', ['chassis'])
    op.create_index('ix_vehicles_plate', 'vehicles', ['plate'], unique=True)
    op.create_index('ix_clients_cpf_cnpj', 'clients', ['cpf_cnpj'], unique=True)
