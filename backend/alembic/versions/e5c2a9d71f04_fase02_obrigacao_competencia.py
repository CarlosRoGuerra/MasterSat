"""Fase 02: competência canônica, substituição e integridade financeira

Aditiva. O que muda em ``billings``:

* ``competencia`` (DATE) — primeiro dia do mês em que o período começa,
  calculado do ``period_label`` pela função ``mastersat_competencia`` e
  mantido pelo trigger ``trg_billings_competencia`` (inclusive para quem não
  conhece a coluna: código antigo num rollback, SQL manual). FIN-04.
* ``competencia_liberada`` (BOOL) — mensalidade cancelada cujo mês o
  financeiro devolveu para nova cobrança. Default false = comportamento atual
  (cancelada ocupa o mês). FIN-01.
* ``substituted_by_id`` (FK billings) — título que assumiu a dívida (boleto
  único do fechamento, negociação). Backfill a partir dos marcadores que o
  próprio sistema grava em ``notes``:
  "Consolidada no boleto único #N." e "Unificada na cobrança #N.". FIN-01.
* índice ``uq_billings_contract_competencia_recorrente`` no lugar de
  ``uq_billings_contract_period_recurring`` (texto): '9/2026' e '09/2026'
  passam a ser o mesmo mês. FIN-04.
* índice ``uq_billings_item_parcela_efetiva`` — uma parcela efetiva por
  serviço avulso. FIN-12.
* CHECKs de valor/parcela/coerência em billings, client_charge_items e
  billing_charge_items. DB-02.

PREFLIGHT: antes de qualquer alteração a migration procura, nos dados atuais,
tudo que faria um índice ou CHECK falhar. Se achar, aborta listando IDs e
valores — não apaga, não cancela, não funde nada. O mesmo relatório sai, sem
alterar o banco, com::

    python scripts/preflight_fase02.py        # dentro do container backend

Saneamento de cada caso: docs/financeiro/saneamento-fase-02.md.

Revision ID: e5c2a9d71f04
Revises: d9e4f1a7b2c5
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e5c2a9d71f04'
down_revision: Union[str, None] = 'd9e4f1a7b2c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RECURRING = "('recorrente','prorata','primeira_mensalidade','carne')"
_LOTE_BACKFILL = 5000

# Cópia congelada da regra de app/services/competencia.py. A migration não
# importa o módulo da aplicação: se a regra mudar no futuro, esta revisão
# continua fazendo o que fazia quando foi aplicada. O teste
# test_fase02_migrations_postgres.py::test_parser_python_e_sql_concordam compara as duas.
SQL_FUNCAO_COMPETENCIA = r"""
CREATE OR REPLACE FUNCTION {fn}(rotulo text) RETURNS date
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $fn$
DECLARE
    r text;
    m text[];
    ano int;
    mes int;
BEGIN
    IF rotulo IS NULL THEN
        RETURN NULL;
    END IF;
    r := btrim(rotulo, E' \t');
    m := regexp_match(r, '^([0-9]{{1,2}}) *[/.-] *([0-9]{{4}})$');
    IF m IS NOT NULL THEN
        ano := m[2]::int; mes := m[1]::int;
    ELSE
        m := regexp_match(r, '^([0-9]{{4}}) *[/-] *([0-9]{{1,2}})$');
        IF m IS NOT NULL THEN
            ano := m[1]::int; mes := m[2]::int;
        ELSE
            m := regexp_match(r, '^([0-9]{{4}}) *• *[Tt]([1-4])$');
            IF m IS NOT NULL THEN
                ano := m[1]::int; mes := (m[2]::int - 1) * 3 + 1;
            ELSE
                m := regexp_match(r, '^([0-9]{{4}}) *• *[Ss]([12])$');
                IF m IS NOT NULL THEN
                    ano := m[1]::int; mes := (m[2]::int - 1) * 6 + 1;
                ELSE
                    m := regexp_match(r, '^([0-9]{{4}})$');
                    IF m IS NOT NULL THEN
                        ano := m[1]::int; mes := 1;
                    ELSE
                        RETURN NULL;
                    END IF;
                END IF;
            END IF;
        END IF;
    END IF;
    IF mes < 1 OR mes > 12 OR ano < 1900 OR ano > 2199 THEN
        RETURN NULL;
    END IF;
    RETURN make_date(ano, mes, 1);
END
$fn$
"""

_SQL_FUNCAO_TRIGGER = r"""
CREATE OR REPLACE FUNCTION mastersat_billings_competencia() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.period_label IS NOT NULL THEN
        NEW.competencia := mastersat_competencia(NEW.period_label);
    END IF;
    RETURN NEW;
END
$fn$
"""

_SQL_TRIGGER = """
CREATE TRIGGER trg_billings_competencia
BEFORE INSERT OR UPDATE OF period_label ON billings
FOR EACH ROW EXECUTE FUNCTION mastersat_billings_competencia()
"""

# Substituto derivado das notas, só para canceladas e só quando o alvo existe.
# Ignora o marcador quando a própria nota registra que aquela substituição foi
# revertida (financial.restore_substituted_originals): a original reaberta e
# cancelada de novo depois não é mais substituída por aquele título. Isso
# importa num re-upgrade após rollback (downgrade → código antigo → upgrade).
_SQL_SUBSTITUICAO_DERIVADA = """
SELECT b.id, alvo.id AS substituto, alvo.is_deleted AS substituto_removido,
       alvo.status::text AS substituto_status
FROM (
    SELECT id, notes,
           COALESCE(
               (regexp_match(notes, 'Unificada na cobrança #([0-9]+)\\.'))[1],
               (regexp_match(notes, 'Consolidada no boleto único #([0-9]+)\\.'))[1]
           )::int AS alvo_id
    FROM billings
    WHERE status = 'CANCELED' AND notes IS NOT NULL
) b
JOIN billings alvo ON alvo.id = b.alvo_id AND alvo.id <> b.id
WHERE position('substituição pela cobrança #' || b.alvo_id::text || ' revertida' IN b.notes) = 0
"""

# Competências liberadas guardadas pelo downgrade (o schema antigo não tem a
# coluna). O upgrade seguinte as devolve e apaga a tabela.
_TABELA_LIBERADAS = 'fase02_competencias_liberadas'

# Cada verificação: (código, descrição, SQL que devolve as linhas violadoras,
# bloqueia?). As bloqueantes são exatamente as que fariam um índice/CHECK desta
# migration falhar. As demais são inventário para o saneamento.
def _verificacoes(fn: str) -> list[tuple[str, str, str, bool]]:
    return [
        (
            'mensalidade_duplicada',
            'Mais de uma mensalidade/pró-rata/1ª cobrança/carnê não removida para o mesmo '
            'contrato e competência (canceladas inclusive — elas ocupam o mês)',
            f"""
            SELECT contract_id, {fn}(period_label) AS competencia,
                   array_agg(id ORDER BY id) AS ids,
                   array_agg(period_label ORDER BY id) AS rotulos,
                   array_agg(status::text ORDER BY id) AS status,
                   array_agg(amount::text ORDER BY id) AS valores
            FROM billings
            WHERE is_deleted = false AND contract_id IS NOT NULL
              AND billing_type IN {_RECURRING}
              AND {fn}(period_label) IS NOT NULL
            GROUP BY contract_id, {fn}(period_label)
            HAVING COUNT(*) > 1
            ORDER BY contract_id, 2
            """,
            True,
        ),
        (
            'parcela_servico_duplicada',
            'Mais de uma parcela efetiva (não removida; não cancelada ou cancelada por '
            'substituição) para o mesmo serviço e número de parcela',
            f"""
            WITH sub AS ({_SQL_SUBSTITUICAO_DERIVADA})
            SELECT b.item_id, b.installment_number,
                   array_agg(b.id ORDER BY b.id) AS ids,
                   array_agg(b.status::text ORDER BY b.id) AS status,
                   array_agg(b.amount::text ORDER BY b.id) AS valores
            FROM billings b
            LEFT JOIN sub ON sub.id = b.id
            WHERE b.is_deleted = false AND b.item_id IS NOT NULL
              AND b.installment_number IS NOT NULL
              AND (b.status <> 'CANCELED' OR sub.substituto IS NOT NULL)
            GROUP BY b.item_id, b.installment_number
            HAVING COUNT(*) > 1
            ORDER BY b.item_id, b.installment_number
            """,
            True,
        ),
        (
            'cobranca_valor_invalido',
            'Cobrança com valor ≤ 0 ou valor pago ≤ 0',
            """
            SELECT id, amount::text AS valor, paid_amount::text AS valor_pago,
                   status::text AS status, is_deleted
            FROM billings
            WHERE amount <= 0 OR paid_amount <= 0
            ORDER BY id
            """,
            True,
        ),
        (
            'cobranca_parcela_invalida',
            'Cobrança com número de parcela < 1 ou maior que o total de parcelas',
            """
            SELECT id, installment_number, installment_total, status::text AS status, is_deleted
            FROM billings
            WHERE installment_number < 1 OR installment_number > installment_total
            ORDER BY id
            """,
            True,
        ),
        (
            'servico_avulso_invalido',
            'Serviço avulso com quantidade/parcelas < 1 ou preço/total ≤ 0',
            """
            SELECT id, quantity, installment_count, unit_price::text AS preco,
                   total_amount::text AS total, is_deleted
            FROM client_charge_items
            WHERE quantity < 1 OR installment_count < 1 OR unit_price <= 0 OR total_amount <= 0
            ORDER BY id
            """,
            True,
        ),
        (
            'vinculo_servico_invalido',
            'Vínculo cobrança↔serviço com valor ≤ 0',
            """
            SELECT id, billing_id, item_id, amount::text AS valor
            FROM billing_charge_items
            WHERE amount <= 0
            ORDER BY id
            """,
            True,
        ),
        (
            'mensalidade_sem_competencia',
            'Mensalidade com rótulo de período fora de formato — fica sem competência e '
            'fora do índice único; revisar e corrigir o rótulo',
            f"""
            SELECT id, contract_id, period_label, status::text AS status, due_date
            FROM billings
            WHERE is_deleted = false AND billing_type IN {_RECURRING}
              AND {fn}(period_label) IS NULL
            ORDER BY id
            """,
            False,
        ),
        (
            'substituida_sem_substituto_efetivo',
            'Original cancelada por consolidação/negociação cujo substituto foi removido '
            'ou cancelado — o período está sem cobrança em aberto (FIN-01)',
            f"""
            WITH sub AS ({_SQL_SUBSTITUICAO_DERIVADA})
            SELECT b.id, b.contract_id, b.period_label, b.amount::text AS valor,
                   sub.substituto, sub.substituto_removido, sub.substituto_status
            FROM billings b
            JOIN sub ON sub.id = b.id
            WHERE b.is_deleted = false
              AND (sub.substituto_removido OR sub.substituto_status = 'CANCELED')
            ORDER BY b.id
            """,
            False,
        ),
        (
            'marcador_sem_alvo',
            'Cancelada com marcador de substituição apontando para cobrança inexistente '
            '(o vínculo não é gravado)',
            """
            SELECT b.id, b.notes
            FROM billings b
            WHERE b.status = 'CANCELED' AND b.notes IS NOT NULL
              AND (b.notes ~ 'Unificada na cobrança #[0-9]+\\.'
                   OR b.notes ~ 'Consolidada no boleto único #[0-9]+\\.')
              AND NOT EXISTS (
                  SELECT 1 FROM billings alvo
                  WHERE alvo.id <> b.id AND alvo.id = COALESCE(
                      (regexp_match(b.notes, 'Unificada na cobrança #([0-9]+)\\.'))[1],
                      (regexp_match(b.notes, 'Consolidada no boleto único #([0-9]+)\\.'))[1]
                  )::int
              )
            ORDER BY b.id
            """,
            False,
        ),
    ]


def coletar_violacoes(conn, fn: str = 'mastersat_competencia') -> list[dict]:
    """Roda todas as verificações. ``fn`` é a função de competência a usar —
    o script de preflight cria uma cópia temporária (pg_temp) para não
    alterar o banco."""
    resultado = []
    for codigo, descricao, sql, bloqueia in _verificacoes(fn):
        linhas = [dict(row._mapping) for row in conn.execute(sa.text(sql))]
        resultado.append({
            'codigo': codigo,
            'descricao': descricao,
            'bloqueia': bloqueia,
            'total': len(linhas),
            'linhas': linhas,
        })
    return resultado


def formatar_relatorio(violacoes: list[dict], limite_por_item: int = 50) -> str:
    partes = []
    for v in violacoes:
        if not v['total']:
            continue
        marca = 'BLOQUEIA' if v['bloqueia'] else 'inventário'
        partes.append(f"[{marca}] {v['codigo']}: {v['total']} — {v['descricao']}")
        for linha in v['linhas'][:limite_por_item]:
            partes.append('    ' + ', '.join(f'{k}={linha[k]}' for k in linha))
        if v['total'] > limite_por_item:
            partes.append(f"    ... mais {v['total'] - limite_por_item} (rode scripts/preflight_fase02.py)")
    return '\n'.join(partes)


def _preflight_upgrade(bind) -> None:
    violacoes = coletar_violacoes(bind)
    bloqueantes = [v for v in violacoes if v['bloqueia'] and v['total']]
    if bloqueantes:
        raise RuntimeError(
            'Migration e5c2a9d71f04 abortada ANTES de alterar o banco: há dados que '
            'violariam as novas garantias. Nada foi apagado ou cancelado. Resolva cada '
            'caso conforme docs/financeiro/saneamento-fase-02.md e reaplique.\n'
            + formatar_relatorio(bloqueantes)
        )


def upgrade() -> None:
    bind = op.get_bind()
    op.execute(SQL_FUNCAO_COMPETENCIA.format(fn='mastersat_competencia'))
    _preflight_upgrade(bind)

    op.add_column('billings', sa.Column('competencia', sa.Date(), nullable=True))
    op.add_column(
        'billings',
        sa.Column('competencia_liberada', sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column('billings', sa.Column('substituted_by_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_billings_substituted_by_id', 'billings', 'billings', ['substituted_by_id'], ['id'],
    )

    # Backfill em lotes por faixa de id: cada UPDATE toca no máximo
    # _LOTE_BACKFILL linhas. Não dispara o trigger (period_label não muda).
    menor, maior = bind.execute(sa.text('SELECT MIN(id), MAX(id) FROM billings')).one()
    if menor is not None:
        inicio = menor
        while inicio <= maior:
            bind.execute(
                sa.text(
                    'UPDATE billings SET competencia = mastersat_competencia(period_label) '
                    'WHERE id >= :a AND id < :b AND period_label IS NOT NULL'
                ),
                {'a': inicio, 'b': inicio + _LOTE_BACKFILL},
            )
            inicio += _LOTE_BACKFILL
    bind.execute(sa.text(
        f'UPDATE billings b SET substituted_by_id = sub.substituto '
        f'FROM ({_SQL_SUBSTITUICAO_DERIVADA}) sub WHERE sub.id = b.id'
    ))
    if bind.execute(sa.text(f"SELECT to_regclass('{_TABELA_LIBERADAS}') IS NOT NULL")).scalar():
        # Re-upgrade depois de um rollback: devolve as liberações que o
        # downgrade guardou — só para quem continua cancelada.
        bind.execute(sa.text(
            f"UPDATE billings SET competencia_liberada = true "
            f"WHERE status = 'CANCELED' AND id IN (SELECT billing_id FROM {_TABELA_LIBERADAS})"
        ))
        op.drop_table(_TABELA_LIBERADAS)

    op.execute(_SQL_FUNCAO_TRIGGER)
    op.execute(_SQL_TRIGGER)

    op.create_index('ix_billings_competencia', 'billings', ['competencia'])
    op.create_index('ix_billings_substituted_by_id', 'billings', ['substituted_by_id'])
    op.create_index(
        'uq_billings_contract_competencia_recorrente',
        'billings',
        ['contract_id', 'competencia'],
        unique=True,
        postgresql_where=sa.text(
            f'is_deleted = false AND competencia_liberada = false AND billing_type IN {_RECURRING}'
        ),
    )
    op.create_index(
        'uq_billings_item_parcela_efetiva',
        'billings',
        ['item_id', 'installment_number'],
        unique=True,
        postgresql_where=sa.text(
            "is_deleted = false AND (status <> 'CANCELED' OR substituted_by_id IS NOT NULL)"
        ),
    )
    # A garantia textual antiga sai: ela impediria a nova mensalidade de um
    # mês cuja cancelada teve a competência liberada. A nova cobre tudo que
    # ela cobria (rótulo canônico ⇒ mesma competência).
    op.drop_index('uq_billings_contract_period_recurring', table_name='billings')

    op.create_check_constraint('ck_billings_amount_positivo', 'billings', 'amount > 0')
    op.create_check_constraint(
        'ck_billings_paid_amount_positivo', 'billings', 'paid_amount IS NULL OR paid_amount > 0',
    )
    op.create_check_constraint(
        'ck_billings_parcela_no_intervalo', 'billings',
        'installment_number IS NULL OR (installment_number >= 1 AND '
        '(installment_total IS NULL OR installment_number <= installment_total))',
    )
    op.create_check_constraint(
        'ck_billings_liberada_so_cancelada', 'billings',
        "NOT competencia_liberada OR status = 'CANCELED'",
    )
    op.create_check_constraint(
        'ck_billings_substituida_cancelada', 'billings',
        "substituted_by_id IS NULL OR (status = 'CANCELED' AND substituted_by_id <> id)",
    )
    op.create_check_constraint('ck_client_charge_items_quantidade', 'client_charge_items', 'quantity >= 1')
    op.create_check_constraint('ck_client_charge_items_parcelas', 'client_charge_items', 'installment_count >= 1')
    op.create_check_constraint('ck_client_charge_items_preco_positivo', 'client_charge_items', 'unit_price > 0')
    op.create_check_constraint('ck_client_charge_items_total_positivo', 'client_charge_items', 'total_amount > 0')
    op.create_check_constraint('ck_billing_charge_items_amount_positivo', 'billing_charge_items', 'amount > 0')


def downgrade() -> None:
    # O índice textual antigo volta. Se o financeiro já liberou alguma
    # competência e lançou a nova mensalidade, as duas têm o mesmo rótulo e o
    # índice antigo não pode ser recriado: aborta listando, sem apagar nada.
    bind = op.get_bind()
    conflitos = bind.execute(sa.text(f"""
        SELECT contract_id, period_label, array_agg(id ORDER BY id) AS ids
        FROM billings
        WHERE is_deleted = false AND billing_type IN {_RECURRING}
          AND contract_id IS NOT NULL AND period_label IS NOT NULL
        GROUP BY contract_id, period_label
        HAVING COUNT(*) > 1
    """)).fetchall()
    if conflitos:
        linhas = '\n'.join(f'    contrato={c} rótulo={r} ids={i}' for c, r, i in conflitos)
        raise RuntimeError(
            'Downgrade de e5c2a9d71f04 abortado: o índice antigo (contrato + texto do rótulo, '
            'canceladas incluídas) não comporta estas cobranças. Normalmente é uma competência '
            'liberada seguida de nova mensalidade. Nada foi alterado.\n' + linhas
        )

    for nome, tabela in (
        ('ck_billing_charge_items_amount_positivo', 'billing_charge_items'),
        ('ck_client_charge_items_total_positivo', 'client_charge_items'),
        ('ck_client_charge_items_preco_positivo', 'client_charge_items'),
        ('ck_client_charge_items_parcelas', 'client_charge_items'),
        ('ck_client_charge_items_quantidade', 'client_charge_items'),
        ('ck_billings_substituida_cancelada', 'billings'),
        ('ck_billings_liberada_so_cancelada', 'billings'),
        ('ck_billings_parcela_no_intervalo', 'billings'),
        ('ck_billings_paid_amount_positivo', 'billings'),
        ('ck_billings_amount_positivo', 'billings'),
    ):
        op.drop_constraint(nome, tabela, type_='check')

    op.create_index(
        'uq_billings_contract_period_recurring',
        'billings',
        ['contract_id', 'period_label'],
        unique=True,
        postgresql_where=sa.text(f'is_deleted = false AND billing_type IN {_RECURRING}'),
    )
    op.drop_index('uq_billings_item_parcela_efetiva', table_name='billings')
    op.drop_index('uq_billings_contract_competencia_recorrente', table_name='billings')
    op.drop_index('ix_billings_substituted_by_id', table_name='billings')
    op.drop_index('ix_billings_competencia', table_name='billings')

    op.execute('DROP TRIGGER IF EXISTS trg_billings_competencia ON billings')
    op.execute('DROP FUNCTION IF EXISTS mastersat_billings_competencia()')
    op.execute('DROP FUNCTION IF EXISTS mastersat_competencia(text)')

    # Os vínculos de substituição continuam legíveis nas notas (o sistema
    # segue gravando os marcadores e o de reversão) e o upgrade os refaz. As
    # competências liberadas não cabem no schema antigo: ficam guardadas numa
    # tabela à parte até o próximo upgrade. Nada é apagado.
    bind.execute(sa.text(
        f'CREATE TABLE IF NOT EXISTS {_TABELA_LIBERADAS} ('
        f'billing_id integer PRIMARY KEY, preservado_em timestamptz NOT NULL DEFAULT now())'
    ))
    bind.execute(sa.text(
        f'INSERT INTO {_TABELA_LIBERADAS} (billing_id) '
        f'SELECT id FROM billings WHERE competencia_liberada ON CONFLICT DO NOTHING'
    ))
    op.drop_constraint('fk_billings_substituted_by_id', 'billings', type_='foreignkey')
    op.drop_column('billings', 'substituted_by_id')
    op.drop_column('billings', 'competencia_liberada')
    op.drop_column('billings', 'competencia')
