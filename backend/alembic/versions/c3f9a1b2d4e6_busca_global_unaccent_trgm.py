"""busca global — unaccent + pg_trgm

Suporte de banco pra Busca Global (Command Palette, ver
app/services/global_search.py): extensão `unaccent` (nome/marca/modelo sem
acento encontram registros acentuados) e `pg_trgm` (acelera os `ILIKE
'%termo%'` que a busca já faz nessas mesmas colunas — sem índice btree ajuda,
já que o termo não é prefixo).

Revision ID: c3f9a1b2d4e6
Revises: 8b4e2a7f1c93
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c3f9a1b2d4e6'
down_revision: Union[str, None] = '8b4e2a7f1c93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TRGM_INDEXES = [
    ('ix_clients_name_trgm', 'clients', 'name'),
    ('ix_clients_trade_name_trgm', 'clients', 'trade_name'),
    ('ix_vehicles_plate_trgm', 'vehicles', 'plate'),
    ('ix_vehicles_brand_trgm', 'vehicles', 'brand'),
    ('ix_vehicles_model_trgm', 'vehicles', 'model'),
    ('ix_trackers_imei_trgm', 'trackers', 'imei'),
    ('ix_trackers_serial_number_trgm', 'trackers', 'serial_number'),
    ('ix_service_orders_number_trgm', 'service_orders', 'number'),
]


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS unaccent')
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    for index_name, table_name, column_name in _TRGM_INDEXES:
        op.create_index(
            index_name,
            table_name,
            [column_name],
            postgresql_using='gin',
            postgresql_ops={column_name: 'gin_trgm_ops'},
        )


def downgrade() -> None:
    for index_name, table_name, _ in reversed(_TRGM_INDEXES):
        op.drop_index(index_name, table_name=table_name)
    # As extensões ficam — outras features podem passar a depender delas, e
    # DROP EXTENSION é uma operação destrutiva demais pra um downgrade rotineiro.
