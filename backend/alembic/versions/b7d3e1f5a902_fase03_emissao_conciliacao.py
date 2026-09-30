"""Fase 03: desfecho bancário, conciliação, ajustes de recebimento e CNAB

Aditiva. Nada é apagado, cancelado nem tem valor alterado; nenhuma chamada
ao banco é feita. O que muda:

* ``ailos_boletos`` ganha estado de registro/conciliação (FIN-02/03/05):
  ``registro_iniciado_em``, checkpoint ``ultima_consulta_em`` (+ erro e
  contador de falhas), baixa (``baixa_status`` pendente/confirmada, datas,
  responsável, observação) e pendência de conciliação.
* ``billing_adjustments`` — desconto, saldo transferido, encargos, crédito e
  estorno de cada recebimento (FIN-06).
* ``payable_change_logs`` — histórico de contas a pagar (FIN-08).
* ``cnab_remessas`` / ``cnab_remessa_itens`` — remessa com sequência, hash e
  reserva de títulos (FIN-07).

INVENTÁRIO (backfill revisável, só flags locais):

* título registrado cuja cobrança está cancelada, removida ou recebida fora
  do boleto → ``baixa_status = 'pendente'``: a conciliação volta a
  acompanhá-lo e a competência não pode ser liberada até a baixa ser
  confirmada (consulta com situação 3/5 ou administrador);
* ``ERRO_REGISTRO`` cuja última tentativa individual não teve resposta
  conclusiva (sem status HTTP ou 5xx em ``ailos_api_logs``) →
  ``DESFECHO_DESCONHECIDO`` + linha em ``billing_change_logs``: a
  conciliação consulta pelo número do documento e resolve sozinha.

O mesmo relatório, sem alterar nada: ``python scripts/preflight_fase03.py``.

ROLLBACK: o downgrade guarda o estado novo de ``ailos_boletos`` em
``fase03_preservado_ailos_boletos`` e renomeia as tabelas novas com dados
para ``fase03_preservado_*``; ``DESFECHO_DESCONHECIDO`` vira ``PROCESSANDO``
(o código antigo bloqueia edição/exclusão de PROCESSANDO). Um novo upgrade
devolve tudo.

Revision ID: b7d3e1f5a902
Revises: e5c2a9d71f04
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b7d3e1f5a902'
down_revision: Union[str, None] = 'e5c2a9d71f04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger('alembic.runtime.migration')

PRESERVADO = 'fase03_preservado_'
_TABELAS_NOVAS = ('billing_adjustments', 'payable_change_logs', 'cnab_remessa_itens', 'cnab_remessas')
_COLUNAS_BOLETO = (
    'registro_iniciado_em', 'ultima_consulta_em', 'ultima_consulta_erro', 'falhas_consulta',
    'baixa_status', 'baixa_solicitada_em', 'baixa_confirmada_em', 'baixa_confirmada_por_user_id',
    'baixa_observacao', 'pendencia', 'pendencia_detalhe', 'pendencia_desde',
)
_MARCA_INVENTARIO = 'Fase 03 (inventário): '

# ---------------------------------------------------------------------------
# Inventário — usado pela migration e por scripts/preflight_fase03.py
# ---------------------------------------------------------------------------

# Título registrado (linha + código) cuja cobrança não está mais em aberto
# por cancelamento, remoção ou recebimento fora do boleto.
SQL_TITULO_ATIVO_SEM_OBRIGACAO = """
SELECT a.billing_id, a.nosso_numero, b.status, b.is_deleted, b.payment_method,
       CASE
         WHEN b.is_deleted THEN 'removida'
         WHEN b.status = 'CANCELED' THEN 'cancelada'
         ELSE 'recebida_fora_do_boleto'
       END AS motivo
FROM ailos_boletos a
JOIN billings b ON b.id = a.billing_id
WHERE a.linha_digitavel IS NOT NULL AND a.codigo_barras IS NOT NULL
  AND {filtro_baixa}
  AND (
        b.is_deleted
     OR b.status = 'CANCELED'
     OR (b.status = 'PAID' AND COALESCE(b.payment_method, '') <> 'boleto')
  )
ORDER BY a.billing_id
"""

# ERRO_REGISTRO cuja última tentativa individual de registro (POST
# gerar/boleto com billing_id) não teve resposta conclusiva.
SQL_ERRO_AMBIGUO = """
SELECT a.billing_id, ult.id AS log_id, ult.status_code, ult.error_message
FROM ailos_boletos a
JOIN LATERAL (
    SELECT l.id, l.status_code, l.error_message
    FROM ailos_api_logs l
    WHERE l.billing_id = a.billing_id
      AND l.method = 'POST'
      AND l.endpoint LIKE '%gerar/boleto%'
    ORDER BY l.id DESC
    LIMIT 1
) ult ON true
WHERE a.status_ailos = 'ERRO_REGISTRO'
  AND a.linha_digitavel IS NULL
  AND (ult.status_code IS NULL OR ult.status_code >= 500 OR ult.status_code = 408)
ORDER BY a.billing_id
"""

_INVENTARIO_SO_LEITURA = [
    (
        'erro_registro_sem_evidencia',
        'ERRO_REGISTRO sem título e sem log individual conclusivo (ex.: lote que falhou): '
        'conferir no internet banking antes de emitir de novo',
        """
        SELECT a.billing_id, a.lote_id, a.updated_at
        FROM ailos_boletos a
        WHERE a.status_ailos = 'ERRO_REGISTRO' AND a.linha_digitavel IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM ailos_api_logs l
            WHERE l.billing_id = a.billing_id AND l.method = 'POST' AND l.endpoint LIKE '%gerar/boleto%'
          )
        ORDER BY a.billing_id
        """,
    ),
    (
        'reserva_orfa',
        'REGISTRANDO/PROCESSANDO há mais de 30 minutos: tratada como desfecho desconhecido e '
        'resolvida pela conciliação',
        """
        SELECT a.billing_id, a.status_ailos, a.updated_at
        FROM ailos_boletos a
        WHERE a.status_ailos IN ('REGISTRANDO', 'PROCESSANDO')
          AND a.linha_digitavel IS NULL
          AND a.updated_at < now() - interval '30 minutes'
        ORDER BY a.billing_id
        """,
    ),
    (
        'paga_com_valor_divergente',
        'Cobrança paga com valor recebido diferente do título e sem ajuste registrado '
        '(histórico anterior à Fase 03: a diferença não tem explicação no sistema)',
        """
        SELECT b.id AS billing_id, b.amount, b.paid_amount, b.payment_date, b.payment_method
        FROM billings b
        WHERE b.is_deleted = false AND b.status = 'PAID'
          AND b.paid_amount IS NOT NULL AND b.paid_amount <> b.amount
        ORDER BY b.id
        """,
    ),
    (
        'conta_a_pagar_inconsistente',
        'Conta a pagar paga sem data de pagamento, cancelada com data de pagamento, ou paga e '
        'removida',
        """
        SELECT p.id AS payable_id, p.status, p.payment_date, p.is_deleted, p.amount
        FROM payables p
        WHERE (p.status = 'paga' AND p.payment_date IS NULL)
           OR (p.status = 'cancelada' AND p.payment_date IS NOT NULL)
           OR (p.status = 'paga' AND p.is_deleted)
        ORDER BY p.id
        """,
    ),
    (
        'carteira_monitorada',
        'Títulos que a conciliação passa a acompanhar (em aberto com nosso número, baixa '
        'pendente ou desfecho pendente)',
        """
        SELECT count(*) AS total
        FROM ailos_boletos a
        JOIN billings b ON b.id = a.billing_id
        WHERE (b.is_deleted = false AND b.status IN ('PENDING', 'OVERDUE') AND a.nosso_numero IS NOT NULL)
           OR a.status_ailos IN ('REGISTRANDO', 'PROCESSANDO', 'ERRO_REGISTRO')
        """,
    ),
]


def _linhas(conn, sql: str) -> list[dict]:
    return [dict(row._mapping) for row in conn.execute(sa.text(sql))]


def coletar_inventario(conn, *, depois_da_migration: bool = False) -> list[dict]:
    """Inventário revisável. ``depois_da_migration`` troca o filtro de baixa
    (antes: coluna ainda não existe)."""
    filtro = "a.baixa_status IS NULL" if depois_da_migration else "true"
    itens = [
        {
            'codigo': 'titulo_ativo_sem_obrigacao',
            'descricao': ('Título registrado no banco com a cobrança cancelada, removida ou recebida '
                          'fora do boleto: recebe baixa pendente (FIN-02)'),
            'acao_migration': "baixa_status = 'pendente'",
            'linhas': _linhas(conn, SQL_TITULO_ATIVO_SEM_OBRIGACAO.format(filtro_baixa=filtro)),
        },
        {
            'codigo': 'erro_registro_ambiguo',
            'descricao': ('ERRO_REGISTRO cuja última tentativa não teve resposta conclusiva '
                          '(sem HTTP, 408 ou 5xx): o boleto pode existir no banco (FIN-03)'),
            'acao_migration': "status_ailos = 'DESFECHO_DESCONHECIDO' + histórico",
            'linhas': _linhas(conn, SQL_ERRO_AMBIGUO),
        },
    ]
    for codigo, descricao, sql in _INVENTARIO_SO_LEITURA:
        itens.append({'codigo': codigo, 'descricao': descricao, 'acao_migration': None,
                      'linhas': _linhas(conn, sql)})
    for item in itens:
        item['total'] = (item['linhas'][0]['total'] if item['codigo'] == 'carteira_monitorada'
                         else len(item['linhas']))
    return itens


# ---------------------------------------------------------------------------

def _tem_tabela(bind, nome: str) -> bool:
    return sa.inspect(bind).has_table(nome)


def _criar_ou_restaurar(bind, nome: str, criar) -> None:
    preservada = f'{PRESERVADO}{nome}'
    if _tem_tabela(bind, preservada):
        op.rename_table(preservada, nome)
        log.info('Fase 03: tabela %s restaurada de %s.', nome, preservada)
    else:
        criar()


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _criar_billing_adjustments() -> None:
    op.create_table(
        'billing_adjustments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('billing_id', sa.Integer(), sa.ForeignKey('billings.id'), nullable=False),
        sa.Column('kind', sa.String(30), nullable=False),
        sa.Column('amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('justification', sa.Text(), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('target_billing_id', sa.Integer(),
                  sa.ForeignKey('billings.id', name='fk_billing_adjustments_target'), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('reversed_at', sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('desconto', 'saldo_transferido', 'encargos', 'credito', 'estorno')",
            name='ck_billing_adjustments_kind',
        ),
        sa.CheckConstraint('amount > 0', name='ck_billing_adjustments_amount_positivo'),
        sa.CheckConstraint(
            "(kind = 'saldo_transferido') = (target_billing_id IS NOT NULL)",
            name='ck_billing_adjustments_saldo_com_destino',
        ),
    )
    op.create_index('ix_billing_adjustments_id', 'billing_adjustments', ['id'])
    op.create_index('ix_billing_adjustments_billing_id', 'billing_adjustments', ['billing_id'])
    op.create_index('ix_billing_adjustments_target_billing_id', 'billing_adjustments', ['target_billing_id'])


def _criar_payable_change_logs() -> None:
    op.create_table(
        'payable_change_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('payable_id', sa.Integer(), sa.ForeignKey('payables.id'), nullable=False),
        sa.Column('changed_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('action', sa.String(20), nullable=False),
        sa.Column('field_name', sa.String(40), nullable=True),
        sa.Column('previous_value', sa.Text(), nullable=True),
        sa.Column('new_value', sa.Text(), nullable=True),
        sa.Column('justification', sa.Text(), nullable=True),
        *_timestamps(),
    )
    op.create_index('ix_payable_change_logs_id', 'payable_change_logs', ['id'])
    op.create_index('ix_payable_change_logs_payable_id', 'payable_change_logs', ['payable_id'])
    op.create_index('ix_payable_change_logs_changed_by_user_id', 'payable_change_logs', ['changed_by_user_id'])


def _criar_cnab_remessas() -> None:
    op.create_table(
        'cnab_remessas',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('layout', sa.String(3), nullable=False),
        sa.Column('sequencial', sa.Integer(), nullable=False),
        sa.Column('arquivo_sha256', sa.String(64), nullable=False),
        sa.Column('arquivo', sa.LargeBinary(), nullable=False),
        sa.Column('total_titulos', sa.Integer(), nullable=False),
        sa.Column('valor_total', sa.Numeric(12, 2), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='gerada'),
        sa.Column('generated_by_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('descartada_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('descarte_motivo', sa.Text(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint('layout', 'sequencial', name='uq_cnab_remessas_layout_sequencial'),
        sa.CheckConstraint("layout IN ('240', '400')", name='ck_cnab_remessas_layout'),
        sa.CheckConstraint("status IN ('gerada', 'descartada')", name='ck_cnab_remessas_status'),
    )
    op.create_index('ix_cnab_remessas_id', 'cnab_remessas', ['id'])


def _criar_cnab_remessa_itens() -> None:
    op.create_table(
        'cnab_remessa_itens',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('remessa_id', sa.Integer(), sa.ForeignKey('cnab_remessas.id'), nullable=False),
        sa.Column('billing_id', sa.Integer(), sa.ForeignKey('billings.id'), nullable=False),
        sa.Column('valor', sa.Numeric(10, 2), nullable=False),
        sa.Column('vencimento', sa.Date(), nullable=True),
        sa.Column('nosso_numero', sa.String(40), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='reservado'),
        *_timestamps(),
        sa.UniqueConstraint('remessa_id', 'billing_id', name='uq_cnab_remessa_itens_billing'),
        sa.CheckConstraint("status IN ('reservado', 'liberado')", name='ck_cnab_remessa_itens_status'),
    )
    op.create_index('ix_cnab_remessa_itens_id', 'cnab_remessa_itens', ['id'])
    op.create_index('ix_cnab_remessa_itens_remessa_id', 'cnab_remessa_itens', ['remessa_id'])
    op.create_index('ix_cnab_remessa_itens_billing_id', 'cnab_remessa_itens', ['billing_id'])
    op.create_index(
        'uq_cnab_remessa_itens_billing_reservado', 'cnab_remessa_itens', ['billing_id'],
        unique=True, postgresql_where=sa.text("status = 'reservado'"),
    )


def upgrade() -> None:
    bind = op.get_bind()

    # ── ailos_boletos ────────────────────────────────────────────────────
    with op.batch_alter_table('ailos_boletos') as batch:
        batch.add_column(sa.Column('registro_iniciado_em', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('ultima_consulta_em', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('ultima_consulta_erro', sa.Text(), nullable=True))
        batch.add_column(sa.Column('falhas_consulta', sa.Integer(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('baixa_status', sa.String(20), nullable=True))
        batch.add_column(sa.Column('baixa_solicitada_em', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('baixa_confirmada_em', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('baixa_confirmada_por_user_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('baixa_observacao', sa.Text(), nullable=True))
        batch.add_column(sa.Column('pendencia', sa.String(40), nullable=True))
        batch.add_column(sa.Column('pendencia_detalhe', sa.JSON(), nullable=True))
        batch.add_column(sa.Column('pendencia_desde', sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key('fk_ailos_boletos_baixa_user', 'users', ['baixa_confirmada_por_user_id'], ['id'])
        batch.create_check_constraint(
            'ck_ailos_boletos_baixa_status',
            "baixa_status IS NULL OR baixa_status IN ('pendente', 'confirmada')",
        )
    op.create_index('ix_ailos_boletos_ultima_consulta_em', 'ailos_boletos', ['ultima_consulta_em'])
    op.create_index('ix_ailos_boletos_baixa_status', 'ailos_boletos', ['baixa_status'])
    op.create_index('ix_ailos_boletos_pendencia', 'ailos_boletos', ['pendencia'])

    # ── tabelas novas (ou devolvidas por um rollback anterior) ───────────
    _criar_ou_restaurar(bind, 'billing_adjustments', _criar_billing_adjustments)
    _criar_ou_restaurar(bind, 'payable_change_logs', _criar_payable_change_logs)
    _criar_ou_restaurar(bind, 'cnab_remessas', _criar_cnab_remessas)
    _criar_ou_restaurar(bind, 'cnab_remessa_itens', _criar_cnab_remessa_itens)

    # ── estado preservado por um downgrade anterior ──────────────────────
    preservado = f'{PRESERVADO}ailos_boletos'
    if _tem_tabela(bind, preservado):
        colunas = ', '.join(f'{c} = p.{c}' for c in _COLUNAS_BOLETO)
        op.execute(sa.text(f'UPDATE ailos_boletos a SET {colunas} FROM {preservado} p WHERE a.billing_id = p.billing_id'))
        # Desfecho desconhecido que o código antigo não resolveu volta a sê-lo.
        op.execute(sa.text(f"""
            UPDATE ailos_boletos a SET status_ailos = 'DESFECHO_DESCONHECIDO'
            FROM {preservado} p
            WHERE a.billing_id = p.billing_id
              AND p.status_ailos = 'DESFECHO_DESCONHECIDO'
              AND a.status_ailos = 'PROCESSANDO'
              AND a.linha_digitavel IS NULL
        """))
        op.drop_table(preservado)
        log.info('Fase 03: estado de ailos_boletos restaurado de %s.', preservado)

    # ── inventário (flags locais revisáveis) ─────────────────────────────
    ativos = _linhas(bind, SQL_TITULO_ATIVO_SEM_OBRIGACAO.format(filtro_baixa='a.baixa_status IS NULL'))
    for linha in ativos:
        bind.execute(sa.text("""
            UPDATE ailos_boletos
            SET baixa_status = 'pendente', baixa_solicitada_em = now(), baixa_observacao = :obs
            WHERE billing_id = :bid AND baixa_status IS NULL
        """), {'bid': linha['billing_id'],
               'obs': f"{_MARCA_INVENTARIO}cobrança {linha['motivo']} com título registrado; confirmar baixa no banco"})
    ambiguos = _linhas(bind, SQL_ERRO_AMBIGUO)
    for linha in ambiguos:
        bind.execute(sa.text("""
            UPDATE ailos_boletos SET status_ailos = 'DESFECHO_DESCONHECIDO'
            WHERE billing_id = :bid AND status_ailos = 'ERRO_REGISTRO' AND linha_digitavel IS NULL
        """), {'bid': linha['billing_id']})
        bind.execute(sa.text("""
            INSERT INTO billing_change_logs
                (billing_id, changed_by_user_id, field_name, previous_value, new_value, justification)
            VALUES (:bid, NULL, 'registro_ailos', 'ERRO_REGISTRO', 'DESFECHO_DESCONHECIDO', :just)
        """), {'bid': linha['billing_id'],
               'just': (f"{_MARCA_INVENTARIO}última tentativa de registro sem resposta conclusiva "
                        f"(ailos_api_logs #{linha['log_id']}, HTTP {linha['status_code'] or '—'}); "
                        'a conciliação consulta pelo número do documento')})
    log.info('Fase 03: %s título(s) com baixa pendente; %s registro(s) com desfecho desconhecido.',
             len(ativos), len(ambiguos))


def downgrade() -> None:
    bind = op.get_bind()
    preservado = f'{PRESERVADO}ailos_boletos'

    # Estado novo de ailos_boletos guardado para um upgrade futuro.
    colunas = ', '.join(_COLUNAS_BOLETO)
    op.execute(sa.text(f"""
        CREATE TABLE {preservado} AS
        SELECT billing_id, status_ailos, {colunas}
        FROM ailos_boletos
        WHERE registro_iniciado_em IS NOT NULL OR ultima_consulta_em IS NOT NULL
           OR baixa_status IS NOT NULL OR pendencia IS NOT NULL
           OR status_ailos = 'DESFECHO_DESCONHECIDO'
    """))
    total = bind.execute(sa.text(f'SELECT count(*) FROM {preservado}')).scalar()
    if not total:
        op.drop_table(preservado)
    # O código antigo trata PROCESSANDO como "em registro": bloqueia editar,
    # cancelar e remover. É o mais próximo de desfecho desconhecido que ele tem.
    op.execute(sa.text(
        "UPDATE ailos_boletos SET status_ailos = 'PROCESSANDO' WHERE status_ailos = 'DESFECHO_DESCONHECIDO'"
    ))

    op.drop_index('ix_ailos_boletos_pendencia', table_name='ailos_boletos')
    op.drop_index('ix_ailos_boletos_baixa_status', table_name='ailos_boletos')
    op.drop_index('ix_ailos_boletos_ultima_consulta_em', table_name='ailos_boletos')
    with op.batch_alter_table('ailos_boletos') as batch:
        batch.drop_constraint('ck_ailos_boletos_baixa_status', type_='check')
        batch.drop_constraint('fk_ailos_boletos_baixa_user', type_='foreignkey')
        for coluna in reversed(_COLUNAS_BOLETO):
            batch.drop_column(coluna)

    # Tabelas novas: vazias somem; com dados, ficam guardadas (nada se perde).
    for nome in _TABELAS_NOVAS:
        linhas = bind.execute(sa.text(f'SELECT count(*) FROM {nome}')).scalar()
        if linhas:
            op.rename_table(nome, f'{PRESERVADO}{nome}')
            log.info('Fase 03: %s (%s linha(s)) preservada como %s%s.', nome, linhas, PRESERVADO, nome)
        else:
            op.drop_table(nome)
