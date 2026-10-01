"""Competência canônica de uma cobrança.

``Billing.period_label`` é texto livre desde o início (tela, importação SGR,
fechamento). A unicidade da mensalidade comparava esse texto literalmente e
por isso ``09/2026`` e ``9/2026`` eram dois meses diferentes para o banco
(FIN-04). A competência passa a ser uma data — o primeiro dia do mês em que o
período começa — calculada sempre pela mesma regra:

* aqui, em Python, para a aplicação e para o SQLite dos testes;
* na função ``mastersat_competencia`` do PostgreSQL (migration
  ``e5c2a9d71f04``), chamada por trigger em todo INSERT/UPDATE do rótulo.
  Assim até um escritor que não conhece a coluna (código antigo num rollback,
  SQL manual) grava a competência certa.

As duas implementações precisam aceitar exatamente os mesmos formatos — o
teste ``test_fase02_migrations_postgres.py::test_parser_python_e_sql_concordam`` compara
as duas.

Rótulos aceitos (espaços nas bordas e em volta dos separadores são ignorados):

=====================  ===============================  ==================
formato                exemplos                         competência
=====================  ===============================  ==================
mês/ano                ``09/2026``, ``9/2026``,         2026-09-01
                       ``9-2026``, ``09.2026``
ano-mês                ``2026-09``, ``2026/9``          2026-09-01
trimestre              ``2026 • T3``                    2026-07-01
semestre               ``2026 • S2``                    2026-07-01
ano                    ``2026``                         2026-01-01
=====================  ===============================  ==================

Qualquer outra coisa não tem competência (``None``). Para os tipos que
ocupam o mês do contrato isso é recusado na API; no legado aparece no
relatório de saneamento (``scripts/preflight_fase02.py``).
"""
from __future__ import annotations

import re
from datetime import date

_ANO_MIN = 1900
_ANO_MAX = 2199

# [0-9] e espaço literal, não \d/\s: em Python eles aceitam dígitos e espaços
# Unicode, e a função SQL precisa se comportar igual.
_MES_ANO = re.compile(r'^([0-9]{1,2}) *[/.-] *([0-9]{4})$')
_ANO_MES = re.compile(r'^([0-9]{4}) *[/-] *([0-9]{1,2})$')
_TRIMESTRE = re.compile(r'^([0-9]{4}) *• *[Tt]([1-4])$')
_SEMESTRE = re.compile(r'^([0-9]{4}) *• *[Ss]([12])$')
_ANO = re.compile(r'^([0-9]{4})$')


def _ano_mes(rotulo: str) -> tuple[int, int, int] | None:
    """(ano, mês inicial, meses do período) ou None."""
    texto = rotulo.strip(' \t')
    if m := _MES_ANO.match(texto):
        return int(m.group(2)), int(m.group(1)), 1
    if m := _ANO_MES.match(texto):
        return int(m.group(1)), int(m.group(2)), 1
    if m := _TRIMESTRE.match(texto):
        return int(m.group(1)), (int(m.group(2)) - 1) * 3 + 1, 3
    if m := _SEMESTRE.match(texto):
        return int(m.group(1)), (int(m.group(2)) - 1) * 6 + 1, 6
    if m := _ANO.match(texto):
        return int(m.group(1)), 1, 12
    return None


def competencia_do_rotulo(rotulo: str | None) -> date | None:
    """Primeiro dia do mês em que o período do rótulo começa, ou None."""
    if rotulo is None:
        return None
    partes = _ano_mes(rotulo)
    if partes is None:
        return None
    ano, mes, _ = partes
    if not (1 <= mes <= 12 and _ANO_MIN <= ano <= _ANO_MAX):
        return None
    return date(ano, mes, 1)


def rotulo_canonico(rotulo: str | None) -> str | None:
    """Mesmo período escrito do jeito que o sistema gera (``09/2026``,
    ``2026 • T3``...). None se o rótulo não tem competência."""
    competencia = competencia_do_rotulo(rotulo)
    if competencia is None:
        return None
    _, _, meses = _ano_mes(rotulo)
    if meses == 12:
        return str(competencia.year)
    if meses == 6:
        return f'{competencia.year} • S{1 if competencia.month == 1 else 2}'
    if meses == 3:
        return f'{competencia.year} • T{(competencia.month - 1) // 3 + 1}'
    return competencia.strftime('%m/%Y')


def competencia_da_data(referencia: date) -> date:
    return referencia.replace(day=1)


# Mesma regra em SQL. Cópia da migration e5c2a9d71f04, usada pelo create_all dos
# testes em PostgreSQL (evento after_create em app/models/billing.py). IMMUTABLE
# porque só depende do argumento — permite usá-la em índice se um dia for preciso.
SQL_FUNCAO_COMPETENCIA = r"""
CREATE OR REPLACE FUNCTION mastersat_competencia(rotulo text) RETURNS date
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
    m := regexp_match(r, '^([0-9]{1,2}) *[/.-] *([0-9]{4})$');
    IF m IS NOT NULL THEN
        ano := m[2]::int; mes := m[1]::int;
    ELSE
        m := regexp_match(r, '^([0-9]{4}) *[/-] *([0-9]{1,2})$');
        IF m IS NOT NULL THEN
            ano := m[1]::int; mes := m[2]::int;
        ELSE
            m := regexp_match(r, '^([0-9]{4}) *• *[Tt]([1-4])$');
            IF m IS NOT NULL THEN
                ano := m[1]::int; mes := (m[2]::int - 1) * 3 + 1;
            ELSE
                m := regexp_match(r, '^([0-9]{4}) *• *[Ss]([12])$');
                IF m IS NOT NULL THEN
                    ano := m[1]::int; mes := (m[2]::int - 1) * 6 + 1;
                ELSE
                    m := regexp_match(r, '^([0-9]{4})$');
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

SQL_FUNCAO_TRIGGER = r"""
CREATE OR REPLACE FUNCTION mastersat_billings_competencia() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    -- O rótulo é a fonte: com rótulo, a competência é sempre a dele. Sem
    -- rótulo, vale a competência que o escritor informou (ou nenhuma).
    IF NEW.period_label IS NOT NULL THEN
        NEW.competencia := mastersat_competencia(NEW.period_label);
    END IF;
    RETURN NEW;
END
$fn$
"""

SQL_TRIGGER = """
CREATE TRIGGER trg_billings_competencia
BEFORE INSERT OR UPDATE OF period_label ON billings
FOR EACH ROW EXECUTE FUNCTION mastersat_billings_competencia()
"""
