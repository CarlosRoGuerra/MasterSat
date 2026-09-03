"""linha do tempo do cliente — índices de performance

Suporte de banco pra Linha do Tempo do Cliente (ver
app/services/client_timeline.py): três colunas usadas em `WHERE ... = X` sem
LIMIT restritivo nunca tiveram índice — `tracker_histories.previous_client_id`
e `.new_client_id` (categoria "rastreador", filtra por
`previous_client_id = X OR new_client_id = X`) e `service_orders.client_id`
(categoria "os", junta com `service_order_status_logs` filtrando por
`ServiceOrder.client_id = X`; esse filtro já existia antes desta feature em
`client_timeline_pdf`/`GET /service-orders?client_id=`, só nunca tinha
índice). Não há tabela nova, não há dado alterado — só 3 índices simples.

Revision ID: a1c9e4f2b6d3
Revises: c3f9a1b2d4e6
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1c9e4f2b6d3'
down_revision: Union[str, None] = 'c3f9a1b2d4e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES = [
    ('ix_tracker_histories_previous_client_id', 'tracker_histories', 'previous_client_id'),
    ('ix_tracker_histories_new_client_id', 'tracker_histories', 'new_client_id'),
    ('ix_service_orders_client_id', 'service_orders', 'client_id'),
]


def upgrade() -> None:
    for index_name, table_name, column_name in _INDEXES:
        op.create_index(index_name, table_name, [column_name])


def downgrade() -> None:
    for index_name, table_name, _ in reversed(_INDEXES):
        op.drop_index(index_name, table_name=table_name)
