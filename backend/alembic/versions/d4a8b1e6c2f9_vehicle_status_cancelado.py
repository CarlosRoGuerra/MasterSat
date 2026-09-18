"""status 'cancelado' em veículos

Distingue, no VehicleStatus, um veículo com contrato CANCELADO de um
RETIRADO (retirado = desinstalação física registrada, com rastreador
desvinculado — ver uninstall_vehicle em vehicles.py; cancelado é só uma
situação de cadastro/contrato, sem esse fluxo). A necessidade veio da base do
SGR (Hinova), cujo campo `situacao_veiculo` já separa ATIVO / CANCELADO /
RETIRADA como valores distintos (ver app/services/sgr_migration/mapping.py) —
o MasterSat só tinha 'retirado' para os dois casos.

Só roda no Postgres: o tipo é um ENUM nativo (`CREATE TYPE vehiclestatus`, ver
a baseline 96f61a589162). No SQLite (testes) `Enum` vira VARCHAR + CHECK
recriado a cada `Base.metadata.create_all()`, então não há tipo para alterar.

`ALTER TYPE ... ADD VALUE` não pode ser revertido (Postgres não permite
remover um valor de enum) — downgrade() é proposital no-op.

Revision ID: d4a8b1e6c2f9
Revises: a1c9e4f2b6d3
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4a8b1e6c2f9'
down_revision: Union[str, None] = 'a1c9e4f2b6d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    op.execute("ALTER TYPE vehiclestatus ADD VALUE IF NOT EXISTS 'CANCELED'")


def downgrade() -> None:
    # Postgres não suporta DROP VALUE em enum — reverter exigiria recriar o
    # tipo inteiro e migrar a coluna. Sem uso real de 'CANCELED' para
    # justificar o risco, o downgrade fica no-op (mesmo padrão aceito em
    # projetos que só ADICIONAM valor de enum via migration).
    pass
