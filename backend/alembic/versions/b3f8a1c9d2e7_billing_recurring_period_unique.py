"""billings: indice unico (contract_id, period_label) para tipos recorrentes

Trava no banco o que a aplicacao ja deveria impedir: dois lancamentos de
mensalidade/carne/prorata para o MESMO contrato no MESMO periodo. E a rede
de seguranca para o bug reportado em producao (carne gerado por
/billings/parcelar duplicando a mensalidade que o fechamento mensal ja
tinha lancado, ou vice-versa) -- mesmo que a aplicacao volte a ter uma
falha de logica ou uma corrida futura, o banco recusa a segunda linha em
vez de aceitar em silencio.

So cobre os tipos que "ocupam" um mes do contrato: recorrente, prorata,
primeira_mensalidade, carne (ver RECURRING_BILLING_TYPES em
app/services/financial.py, fonte unica da verdade tambem em codigo).
Cobrancas avulsas (taxa de instalacao/desinstalacao, servicos, negociacoes)
continuam podendo coexistir com a mensalidade do mesmo periodo -- nao sao a
mesma coisa.

Se a migration abortar por duplicata existente, localize com:

    SELECT contract_id, period_label, array_agg(id) AS billing_ids
    FROM billings
    WHERE is_deleted = false
      AND billing_type IN ('recorrente','prorata','primeira_mensalidade','carne')
    GROUP BY contract_id, period_label
    HAVING COUNT(*) > 1;

Resolva manualmente (cancele/una a duplicata) e reaplique.

Revision ID: b3f8a1c9d2e7
Revises: c7b2f9a41d38
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b3f8a1c9d2e7'
down_revision: Union[str, None] = 'c7b2f9a41d38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RECURRING_TYPES = ('recorrente', 'prorata', 'primeira_mensalidade', 'carne')
_WHERE_CLAUSE = (
    "is_deleted = false AND billing_type IN ('recorrente','prorata','primeira_mensalidade','carne')"
)


def _preflight_upgrade() -> None:
    tipos_sql = ', '.join(f"'{t}'" for t in _RECURRING_TYPES)
    op.execute(
        sa.text(
            f"""
            DO $migration$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM billings
                    WHERE is_deleted = false
                      AND contract_id IS NOT NULL
                      AND period_label IS NOT NULL
                      AND billing_type IN ({tipos_sql})
                    GROUP BY contract_id, period_label
                    HAVING COUNT(*) > 1
                ) THEN
                    RAISE EXCEPTION 'Migration b3f8a1c9d2e7: existem cobrancas duplicadas (mesmo contrato + mesmo periodo, tipo recorrente/prorata/primeira_mensalidade/carne) em billings. Resolva manualmente (cancele ou una as duplicatas) antes de reaplicar esta migration -- ver query de diagnostico no cabecalho do arquivo.';
                END IF;
            END
            $migration$;
            """
        )
    )


def upgrade() -> None:
    _preflight_upgrade()
    op.create_index(
        'uq_billings_contract_period_recurring',
        'billings',
        ['contract_id', 'period_label'],
        unique=True,
        postgresql_where=sa.text(_WHERE_CLAUSE),
    )


def downgrade() -> None:
    op.drop_index('uq_billings_contract_period_recurring', table_name='billings')
