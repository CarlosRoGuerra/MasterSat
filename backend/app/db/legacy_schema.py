"""Diagnóstico de banco pré-Alembic antes do carimbo (DB-03).

Bancos criados antes do Alembic (``create_all`` + ``ensure_schema_updates``)
não têm ``alembic_version``. O boot precisa carimbá-los na revisão que o
schema realmente representa e só então aplicar as migrations seguintes. Até a
Fase 02 a escolha olhava só duas tabelas (``users`` e ``refresh_tokens``): um
banco antigo parcialmente atualizado — sem uma coluna da baseline, por
exemplo — era carimbado como completo e só quebrava depois, em runtime.

Agora o schema é comparado com o manifesto (``manifesto_schema_legado.json``,
gerado das próprias migrations por ``scripts/gerar_manifesto_schema_legado.py``):

* tem de conter TODAS as tabelas, colunas e índices da revisão candidata;
* não pode conter coluna/tabela que só uma migration posterior cria (o
  upgrade seguinte falharia com "already exists", ou pior, pularia uma
  transformação de dados).

Na dúvida, não carimba: o boot para com o diagnóstico. Nada é alterado.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import inspect

MANIFESTO = Path(__file__).with_name('manifesto_schema_legado.json')

BASELINE = '96f61a589162'
BASELINE_COM_REFRESH = 'e0905f77f744'
# Ordem importa: a primeira cujo manifesto o banco satisfaz sem excesso ganha.
REVISOES_LEGADAS = (BASELINE, BASELINE_COM_REFRESH)


@dataclass
class DiagnosticoLegado:
    revisao: str | None
    candidata: str
    faltando: list[str] = field(default_factory=list)
    posteriores: list[str] = field(default_factory=list)
    desconhecidas: list[str] = field(default_factory=list)

    def relatorio(self) -> str:
        linhas = [
            'Banco sem alembic_version com schema que não corresponde a nenhuma '
            'revisão legada admitida — o boot NÃO carimbou nem alterou nada.',
            f'Revisão candidata: {self.candidata}.',
        ]
        if self.faltando:
            linhas.append('Faltam (a revisão candidata cria): ' + ', '.join(self.faltando))
        if self.posteriores:
            linhas.append('Já existem objetos que só migrations posteriores criam: '
                          + ', '.join(self.posteriores))
        if self.desconhecidas:
            linhas.append('Tabelas fora do modelo (não bloqueiam): ' + ', '.join(self.desconhecidas))
        linhas.append(
            'Faça backup, compare com docs/validacao/fase-02.md (seção "Banco legado") e '
            'complete o schema manualmente num ensaio antes de carimbar.'
        )
        return '\n'.join(linhas)


def carregar_manifesto() -> dict:
    return json.loads(MANIFESTO.read_text(encoding='utf-8'))


def _schema_atual(conn) -> dict[str, dict[str, set[str]]]:
    insp = inspect(conn)
    atual = {}
    for tabela in insp.get_table_names():
        if tabela == 'alembic_version':
            continue
        atual[tabela] = {
            'colunas': {col['name'] for col in insp.get_columns(tabela)},
            'indices': {idx['name'] for idx in insp.get_indexes(tabela)},
        }
    return atual


def diagnosticar_schema_legado(conn, metadata=None) -> DiagnosticoLegado:
    """Decide em qual revisão legada o banco pode ser carimbado.

    ``metadata`` (padrão: ``Base.metadata``) representa o head e serve para
    reconhecer objetos criados por migrations posteriores à candidata.
    """
    if metadata is None:
        from app.db.session import Base
        from app.models import registry_all  # noqa: F401 — registra todos os modelos

        metadata = Base.metadata
    manifesto = carregar_manifesto()
    atual = _schema_atual(conn)
    head = {table.name: {col.name for col in table.columns} for table in metadata.sorted_tables}

    diagnosticos = []
    for revisao in REVISOES_LEGADAS:
        esperado = manifesto[revisao]
        faltando = []
        for tabela, spec in esperado.items():
            if tabela not in atual:
                faltando.append(tabela)
                continue
            faltando += [f'{tabela}.{c}' for c in spec['colunas'] if c not in atual[tabela]['colunas']]
            faltando += [f'índice {i}' for i in spec['indices'] if i not in atual[tabela]['indices']]
        posteriores = []
        for tabela, colunas in head.items():
            if tabela not in atual:
                continue
            if tabela not in esperado:
                posteriores.append(tabela)
                continue
            posteriores += [
                f'{tabela}.{c}' for c in sorted(colunas)
                if c in atual[tabela]['colunas'] and c not in esperado[tabela]['colunas']
            ]
        desconhecidas = sorted(t for t in atual if t not in head and t not in esperado)
        diag = DiagnosticoLegado(
            revisao=revisao if not faltando and not posteriores else None,
            candidata=revisao,
            faltando=faltando,
            posteriores=posteriores,
            desconhecidas=desconhecidas,
        )
        if diag.revisao:
            return diag
        diagnosticos.append(diag)
    # Nenhuma serve: devolve o diagnóstico da que chegou mais perto.
    return min(diagnosticos, key=lambda d: len(d.faltando) + len(d.posteriores))


def tem_schema_legado(conn) -> bool:
    """Há alguma tabela da baseline no banco? (sem alembic_version)"""
    manifesto = carregar_manifesto()
    tabelas = set(inspect(conn).get_table_names())
    return bool(tabelas & set(manifesto[BASELINE]))
