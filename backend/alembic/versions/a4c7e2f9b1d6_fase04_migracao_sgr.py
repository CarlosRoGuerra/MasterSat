"""Fase 04: proveniência, conciliação e retomada da migração SGR

Aditiva. Só cria tabelas; nenhuma cobrança, cliente, veículo ou documento
existente é alterado, e nada é inventado para os dados já importados:

* ``sgr_vinculos`` — identidade de origem (cliente, veículo, rastreador,
  contrato, plano) → registro local;
* ``sgr_documentos`` / ``sgr_documento_linhas`` — boleto consolidado do SGR e
  suas linhas (obrigações, descontos e alocação), separado de ``billings``;
* ``sgr_execucoes`` / ``sgr_execucao_unidades`` — rodadas e checkpoint por
  cliente;
* ``sgr_conflitos`` — fila de decisões (transferência, pagamento local ×
  origem, valor alterado na origem);
* ``sgr_arquivos`` — outbox dos PDFs/XMLs com chave de objeto estável.

BACKFILL (fora da migration, revisável): ``python scripts/sgr_backfill.py``
reconstrói documentos/linhas a partir do ``billings.sgr_payload`` preservado,
lista ambiguidades e os documentos cujo desconto foi descartado pela
importação antiga (SGR-01). Não grava nada sem ``--aplicar``. Cliente,
veículo e rastreador importados antes desta fase não têm código de origem
guardado: a identidade deles é criada na próxima rodada, por chave natural,
e só depois da verificação de dono (nunca por inferência ambígua).

ROLLBACK: o downgrade renomeia as tabelas com dados para
``fase04_preservado_*`` e apaga as vazias; um novo upgrade as devolve. O
código antigo não lê nenhuma delas, e os registros importados continuam onde
estão.

Revision ID: a4c7e2f9b1d6
Revises: b7d3e1f5a902
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a4c7e2f9b1d6'
down_revision: Union[str, None] = 'b7d3e1f5a902'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger('alembic.runtime.migration')

PRESERVADO = 'fase04_preservado_'
# Ordem de criação (dependências de FK); o downgrade usa a inversa.
_TABELAS = (
    'sgr_execucoes', 'sgr_execucao_unidades', 'sgr_vinculos', 'sgr_documentos',
    'sgr_documento_linhas', 'sgr_conflitos', 'sgr_arquivos',
)


def _in(coluna: str, valores: tuple[str, ...]) -> str:
    return f"{coluna} IN ({', '.join(repr(v) for v in valores)})"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _criar_sgr_execucoes() -> None:
    op.create_table(
        'sgr_execucoes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('parametros', sa.JSON(), nullable=True),
        sa.Column('retoma_execucao_id', sa.Integer(),
                  sa.ForeignKey('sgr_execucoes.id', name='fk_sgr_execucoes_retoma'), nullable=True),
        sa.Column('versao_codigo', sa.String(64), nullable=True),
        sa.Column('manifesto', sa.JSON(), nullable=True),
        sa.Column('concluida_em', sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            _in('status', ('em_andamento', 'concluida', 'concluida_com_pendencias', 'incompleta', 'falhou')),
            name='ck_sgr_execucoes_status',
        ),
    )


def _criar_sgr_execucao_unidades() -> None:
    op.create_table(
        'sgr_execucao_unidades',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('execucao_id', sa.Integer(), sa.ForeignKey('sgr_execucoes.id'), nullable=False),
        sa.Column('unidade', sa.String(80), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('hash_origem', sa.String(64), nullable=True),
        sa.Column('resumo', sa.JSON(), nullable=True),
        sa.Column('erro', sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            _in('status', ('aplicada', 'bloqueada', 'falhou', 'reaproveitada')),
            name='ck_sgr_execucao_unidades_status',
        ),
    )
    op.create_index('ix_sgr_execucao_unidades_execucao_id', 'sgr_execucao_unidades', ['execucao_id'])
    op.create_index('uq_sgr_execucao_unidades', 'sgr_execucao_unidades', ['execucao_id', 'unidade'], unique=True)


def _criar_sgr_vinculos() -> None:
    op.create_table(
        'sgr_vinculos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('entidade', sa.String(20), nullable=False),
        sa.Column('chave_origem', sa.String(120), nullable=False),
        sa.Column('local_id', sa.Integer(), nullable=False),
        sa.Column('hash_origem', sa.String(64), nullable=True),
        sa.Column('criado_por', sa.String(20), nullable=False),
        sa.Column('primeira_execucao_id', sa.Integer(),
                  sa.ForeignKey('sgr_execucoes.id', name='fk_sgr_vinculos_primeira'), nullable=True),
        sa.Column('ultima_execucao_id', sa.Integer(),
                  sa.ForeignKey('sgr_execucoes.id', name='fk_sgr_vinculos_ultima'), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            _in('entidade', ('cliente', 'veiculo', 'rastreador', 'contrato', 'plano')),
            name='ck_sgr_vinculos_entidade',
        ),
    )
    op.create_index('uq_sgr_vinculos_origem', 'sgr_vinculos', ['entidade', 'chave_origem'], unique=True)
    op.create_index(
        'uq_sgr_vinculos_local', 'sgr_vinculos', ['entidade', 'local_id'], unique=True,
        postgresql_where=sa.text("entidade <> 'plano'"), sqlite_where=sa.text("entidade <> 'plano'"),
    )


def _criar_sgr_documentos() -> None:
    op.create_table(
        'sgr_documentos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('cod_boleto', sa.String(40), nullable=False),
        sa.Column('client_id', sa.Integer(), sa.ForeignKey('clients.id'), nullable=True),
        sa.Column('cod_cliente', sa.String(40), nullable=True),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('motivo_bloqueio', sa.String(60), nullable=True),
        sa.Column('detalhe_bloqueio', sa.JSON(), nullable=True),
        sa.Column('situacao_origem', sa.String(40), nullable=True),
        sa.Column('total_origem_centavos', sa.Integer(), nullable=True),
        sa.Column('descontos_centavos', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('hash_origem', sa.String(64), nullable=True),
        sa.Column('snapshot_origem', sa.JSON(), nullable=True),
        sa.Column('criado_por', sa.String(20), nullable=False),
        sa.Column('ultima_execucao_id', sa.Integer(),
                  sa.ForeignKey('sgr_execucoes.id', name='fk_sgr_documentos_execucao'), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(_in('status', ('conciliado', 'bloqueado')), name='ck_sgr_documentos_status'),
    )
    op.create_index('uq_sgr_documentos_cod_boleto', 'sgr_documentos', ['cod_boleto'], unique=True)
    for coluna in ('client_id', 'cod_cliente', 'status', 'motivo_bloqueio'):
        op.create_index(f'ix_sgr_documentos_{coluna}', 'sgr_documentos', [coluna])


def _criar_sgr_documento_linhas() -> None:
    op.create_table(
        'sgr_documento_linhas',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('documento_id', sa.Integer(), sa.ForeignKey('sgr_documentos.id'), nullable=False),
        sa.Column('chave_linha', sa.String(160), nullable=False),
        sa.Column('placa', sa.String(20), nullable=True),
        sa.Column('produto', sa.String(120), nullable=True),
        sa.Column('mes_referente', sa.String(20), nullable=True),
        sa.Column('tipo', sa.String(20), nullable=False),
        sa.Column('valor_origem_centavos', sa.Integer(), nullable=False),
        sa.Column('desconto_alocado_centavos', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('alocacao', sa.JSON(), nullable=True),
        sa.Column('billing_id', sa.Integer(), sa.ForeignKey('billings.id'), nullable=True),
        sa.Column('hash_local', sa.String(64), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            _in('tipo', ('obrigacao', 'desconto', 'bonificada', 'descartada')),
            name='ck_sgr_documento_linhas_tipo',
        ),
        sa.CheckConstraint(
            "(tipo = 'desconto') = (valor_origem_centavos < 0)",
            name='ck_sgr_documento_linhas_desconto_negativo',
        ),
    )
    op.create_index('ix_sgr_documento_linhas_documento_id', 'sgr_documento_linhas', ['documento_id'])
    op.create_index('uq_sgr_documento_linhas_chave', 'sgr_documento_linhas', ['chave_linha'], unique=True)
    op.create_index(
        'uq_sgr_documento_linhas_billing', 'sgr_documento_linhas', ['billing_id'], unique=True,
        postgresql_where=sa.text('billing_id IS NOT NULL'), sqlite_where=sa.text('billing_id IS NOT NULL'),
    )


def _criar_sgr_conflitos() -> None:
    op.create_table(
        'sgr_conflitos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('execucao_id', sa.Integer(), sa.ForeignKey('sgr_execucoes.id'), nullable=True),
        sa.Column('entidade', sa.String(20), nullable=False),
        sa.Column('chave_origem', sa.String(160), nullable=False),
        sa.Column('tipo', sa.String(40), nullable=False),
        sa.Column('tabela_local', sa.String(40), nullable=True),
        sa.Column('local_id', sa.Integer(), nullable=True),
        sa.Column('detalhe', sa.JSON(), nullable=True),
        sa.Column('hash_origem', sa.String(64), nullable=True),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('resolucao', sa.String(40), nullable=True),
        sa.Column('resolvido_por', sa.String(120), nullable=True),
        sa.Column('justificativa', sa.Text(), nullable=True),
        sa.Column('resolvido_em', sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(_in('status', ('aberto', 'resolvido')), name='ck_sgr_conflitos_status'),
    )
    for coluna in ('execucao_id', 'tipo', 'status'):
        op.create_index(f'ix_sgr_conflitos_{coluna}', 'sgr_conflitos', [coluna])
    op.create_index(
        'uq_sgr_conflitos_aberto', 'sgr_conflitos', ['entidade', 'chave_origem', 'tipo'], unique=True,
        postgresql_where=sa.text("status = 'aberto'"), sqlite_where=sa.text("status = 'aberto'"),
    )


def _criar_sgr_arquivos() -> None:
    op.create_table(
        'sgr_arquivos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tipo', sa.String(20), nullable=False),
        sa.Column('chave_origem', sa.String(120), nullable=False),
        sa.Column('client_id', sa.Integer(), sa.ForeignKey('clients.id'), nullable=False),
        sa.Column('url', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('tentativas', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('ultimo_erro', sa.String(255), nullable=True),
        sa.Column('object_key', sa.String(500), nullable=True),
        sa.Column('document_id', sa.Integer(), sa.ForeignKey('documents.id'), nullable=True),
        sa.Column('tamanho_bytes', sa.Integer(), nullable=True),
        sa.Column('sha256', sa.String(64), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(_in('tipo', ('boleto_pdf', 'nfse_xml')), name='ck_sgr_arquivos_tipo'),
        sa.CheckConstraint(
            _in('status', ('pendente', 'baixado', 'falhou', 'bloqueado', 'sem_url')),
            name='ck_sgr_arquivos_status',
        ),
    )
    op.create_index('uq_sgr_arquivos_origem', 'sgr_arquivos', ['tipo', 'chave_origem'], unique=True)
    op.create_index('ix_sgr_arquivos_client_id', 'sgr_arquivos', ['client_id'])
    op.create_index('ix_sgr_arquivos_status', 'sgr_arquivos', ['status'])


_CRIAR = {
    'sgr_execucoes': _criar_sgr_execucoes,
    'sgr_execucao_unidades': _criar_sgr_execucao_unidades,
    'sgr_vinculos': _criar_sgr_vinculos,
    'sgr_documentos': _criar_sgr_documentos,
    'sgr_documento_linhas': _criar_sgr_documento_linhas,
    'sgr_conflitos': _criar_sgr_conflitos,
    'sgr_arquivos': _criar_sgr_arquivos,
}


def upgrade() -> None:
    bind = op.get_bind()
    inspetor = sa.inspect(bind)
    for nome in _TABELAS:
        preservada = f'{PRESERVADO}{nome}'
        if inspetor.has_table(preservada):
            op.rename_table(preservada, nome)
            log.info('Fase 04: tabela %s restaurada de %s.', nome, preservada)
        else:
            _CRIAR[nome]()
    total = bind.execute(sa.text(
        "SELECT count(*) FROM billings WHERE sgr_payload IS NOT NULL "
        "AND CAST(sgr_payload AS TEXT) <> 'null' AND is_deleted = false "
        "AND id NOT IN (SELECT billing_id FROM sgr_documento_linhas WHERE billing_id IS NOT NULL)"
    )).scalar()
    log.info(
        'Fase 04: %s cobrança(s) importada(s) do SGR sem documento de origem registrado — '
        'rode scripts/sgr_backfill.py (simulação) para o relatório de reconstrução.', total,
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Com qualquer dado, as SETE ficam guardadas (proveniência não se perde, e
    # as FKs entre elas impedem apagar só as vazias); sem dado nenhum, somem.
    linhas = {nome: bind.execute(sa.text(f'SELECT count(*) FROM {nome}')).scalar() for nome in _TABELAS}
    preservar = any(linhas.values())
    for nome in reversed(_TABELAS):
        if preservar:
            op.rename_table(nome, f'{PRESERVADO}{nome}')
            log.info('Fase 04: %s (%s linha(s)) preservada como %s%s.', nome, linhas[nome], PRESERVADO, nome)
        else:
            op.drop_table(nome)
