"""Ensaio de volume da migration b7d3e1f5a902 (Fase 03).

Banco novo na revisão anterior (e5c2a9d71f04) com N cobranças, metade com
título Ailos (a maioria registrada; parte cancelada/removida com título
ativo; parte ERRO_REGISTRO) e 3 linhas de ailos_api_logs por título. Mede
upgrade (colunas + tabelas + inventário), downgrade e novo upgrade.

    TEST_DATABASE_URL=postgresql+psycopg://... python ensaio_volume.py 300000

Só para PostgreSQL descartável.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

N = int(sys.argv[1]) if len(sys.argv) > 1 else 300_000
FASE02 = 'e5c2a9d71f04'
BASE = make_url(os.environ['TEST_DATABASE_URL'])
ADMIN = create_engine(BASE, isolation_level='AUTOCOMMIT')
NOME = f'ensaio_{uuid4().hex[:8]}'
URL = BASE.set(database=NOME).render_as_string(hide_password=False)


def alembic(*args):
    inicio = time.monotonic()
    r = subprocess.run([sys.executable, '-m', 'alembic', *args], env=dict(os.environ, DATABASE_URL=URL),
                       capture_output=True, text=True, cwd='/app')
    return r.returncode, round(time.monotonic() - inicio, 1), r.stderr.strip().splitlines()[-1:] if r.returncode else []


with ADMIN.connect() as conn:
    conn.execute(text(f'CREATE DATABASE "{NOME}"'))
engine = create_engine(URL)
try:
    print('upgrade até e5c2a9d71f04:', alembic('upgrade', FASE02)[:2])
    inicio = time.monotonic()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO clients (name, cpf_cnpj, type, status, is_deleted) "
                          "VALUES ('Ensaio', '52998224725', 'pf', 'ACTIVE', false)"))
        # Avulsas (não ocupam competência). Títulos ficam nos ids ímpares
        # (abaixo); entre eles: 1 em 20 cancelada (g%20=1), 1 em 50 removida
        # (g%50=3), 1 em 40 paga por fora em Pix (g%40=7); pares pagos pelo boleto.
        conn.execute(text("""
            INSERT INTO billings (client_id, billing_type, amount, due_date, status, is_deleted,
                                  payment_method, paid_amount, payment_date)
            SELECT 1, 'avulsa', 80, DATE '2025-01-10' + (g % 700),
                   CASE WHEN g % 20 = 1 THEN 'CANCELED'::billingstatus
                        WHEN g % 40 = 7 OR g % 2 = 0 THEN 'PAID'::billingstatus
                        ELSE 'PENDING'::billingstatus END,
                   g % 50 = 3,
                   CASE WHEN g % 40 = 7 THEN 'pix' WHEN g % 2 = 0 THEN 'boleto' END,
                   CASE WHEN g % 40 = 7 OR g % 2 = 0 THEN 80 END,
                   CASE WHEN g % 40 = 7 OR g % 2 = 0 THEN DATE '2025-02-10' END
            FROM generate_series(1, :n) g
        """), {'n': N})
        # Metade com título: 1 em 25 dessas em ERRO_REGISTRO, o resto registrado.
        conn.execute(text("""
            INSERT INTO ailos_boletos (billing_id, numero_convenio, nosso_numero, linha_digitavel,
                                       codigo_barras, status_ailos)
            SELECT g, '102004',
                   CASE WHEN g % 25 = 1 THEN NULL ELSE 'NN' || g END,
                   CASE WHEN g % 25 = 1 THEN NULL ELSE 'LD' || g END,
                   CASE WHEN g % 25 = 1 THEN NULL ELSE 'CB' || g END,
                   CASE WHEN g % 25 = 1 THEN 'ERRO_REGISTRO' ELSE '0' END
            FROM generate_series(1, :n, 2) g
        """), {'n': N})
        # 3 logs por título; o último de metade dos ERRO_REGISTRO sem HTTP (timeout).
        conn.execute(text("""
            INSERT INTO ailos_api_logs (endpoint, method, status_code, success, billing_id)
            SELECT CASE WHEN k = 3 THEN '/v2/boletos/gerar/boleto/convenios/102004'
                        ELSE '/v2/boletos/consultar/boleto/convenios/102004/' || a.billing_id END,
                   CASE WHEN k = 3 THEN 'POST' ELSE 'GET' END,
                   CASE WHEN k = 3 AND a.status_ailos = 'ERRO_REGISTRO' AND a.billing_id % 2 = 1
                             AND a.billing_id % 4 = 1 THEN NULL
                        WHEN k = 3 AND a.status_ailos = 'ERRO_REGISTRO' THEN 422 ELSE 200 END,
                   a.status_ailos <> 'ERRO_REGISTRO', a.billing_id
            FROM ailos_boletos a CROSS JOIN generate_series(1, 3) k
        """))
        totais = conn.execute(text(
            'SELECT (SELECT count(*) FROM billings), (SELECT count(*) FROM ailos_boletos), '
            '(SELECT count(*) FROM ailos_api_logs)')).one()
    print(f'semeadura: {totais[0]} cobranças, {totais[1]} títulos, {totais[2]} logs em {time.monotonic() - inicio:.1f}s')
    with engine.begin() as conn:
        conn.execute(text('ANALYZE'))

    codigo, dur, erro = alembic('upgrade', 'head')
    print(f'upgrade b7d3e1f5a902 (colunas + tabelas + inventário): exit={codigo} {dur}s {erro}')
    with engine.connect() as conn:
        pendentes = conn.execute(text("SELECT count(*) FROM ailos_boletos WHERE baixa_status = 'pendente'")).scalar_one()
        desconhecidos = conn.execute(text(
            "SELECT count(*) FROM ailos_boletos WHERE status_ailos = 'DESFECHO_DESCONHECIDO'")).scalar_one()
    print(f'inventário: baixa_pendente={pendentes} desfecho_desconhecido={desconhecidos}')
    codigo, dur, erro = alembic('downgrade', FASE02)
    print(f'downgrade para e5c2a9d71f04: exit={codigo} {dur}s {erro}')
    codigo, dur, erro = alembic('upgrade', 'head')
    print(f'novo upgrade: exit={codigo} {dur}s {erro}')
    with engine.connect() as conn:
        pendentes = conn.execute(text("SELECT count(*) FROM ailos_boletos WHERE baixa_status = 'pendente'")).scalar_one()
        desconhecidos = conn.execute(text(
            "SELECT count(*) FROM ailos_boletos WHERE status_ailos = 'DESFECHO_DESCONHECIDO'")).scalar_one()
    print(f'após novo upgrade: baixa_pendente={pendentes} desfecho_desconhecido={desconhecidos}')
finally:
    engine.dispose()
    with ADMIN.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{NOME}" WITH (FORCE)'))
