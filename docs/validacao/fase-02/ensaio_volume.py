"""Ensaio de volume da migration e5c2a9d71f04 (Fase 02).

Banco novo na revisão anterior (d9e4f1a7b2c5), N cobranças sintéticas com
rótulos em vários formatos, originais consolidadas/negociadas com marcador nas
notas e parcelas de serviço. Mede preflight + upgrade e o downgrade.

    TEST_DATABASE_URL=postgresql+psycopg://... python ensaio_volume.py 500000

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

N = int(sys.argv[1]) if len(sys.argv) > 1 else 500_000
BASE = make_url(os.environ['TEST_DATABASE_URL'])
ADMIN = create_engine(BASE, isolation_level='AUTOCOMMIT')
NOME = f'ensaio_{uuid4().hex[:8]}'
URL = BASE.set(database=NOME).render_as_string(hide_password=False)


def alembic(*args):
    inicio = time.monotonic()
    r = subprocess.run([sys.executable, '-m', 'alembic', *args], env=dict(os.environ, DATABASE_URL=URL),
                       capture_output=True, text=True, cwd='/app')
    return r.returncode, time.monotonic() - inicio, r.stderr.strip().splitlines()[-1:] if r.returncode else []


with ADMIN.connect() as conn:
    conn.execute(text(f'CREATE DATABASE "{NOME}"'))
engine = create_engine(URL)
try:
    print('upgrade até d9e4f1a7b2c5:', alembic('upgrade', 'd9e4f1a7b2c5')[:2])
    contratos = max(N // 24, 1)
    inicio = time.monotonic()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO clients (name, cpf_cnpj, type, status, is_deleted) "
                          "VALUES ('Ensaio', '52998224725', 'pf', 'ACTIVE', false)"))
        conn.execute(text("INSERT INTO plans (name, price, active, billing_interval_months, is_deleted) "
                          "VALUES ('Plano Ensaio', 80, true, 1, false)"))
        conn.execute(text(
            "INSERT INTO contracts (client_id, plan_id, start_date, status, billing_modality, signed, is_deleted) "
            "SELECT 1, 1, '2020-01-01', 'ativo', 'boleto', false, false FROM generate_series(1, :n)"
        ), {'n': contratos})
        conn.execute(text(
            "INSERT INTO client_charge_items (client_id, title, quantity, unit_price, total_amount, "
            "installment_count, start_date, active, remove_after_payment, status, is_deleted) "
            "SELECT 1, 'Serviço', 1, 90, 90, 3, '2024-01-10', false, false, 'faturado', false "
            "FROM generate_series(1, :n)"
        ), {'n': N // 20})
        # 24 meses por contrato; formatos de rótulo alternados; 1 em 10 cancelada
        # como original de consolidação (marcador aponta para a linha seguinte).
        conn.execute(text("""
            INSERT INTO billings (client_id, contract_id, billing_type, amount, due_date, status,
                                  is_deleted, period_label, notes)
            SELECT 1, (g - 1) / 24 + 1, 'recorrente', 80,
                   make_date(2023 + ((g - 1) % 24) / 12, ((g - 1) % 12) + 1, 10),
                   CASE WHEN g % 10 = 0 THEN 'CANCELED'::billingstatus ELSE 'PAID'::billingstatus END,
                   false,
                   CASE WHEN g % 3 = 0
                        THEN (((g - 1) % 12) + 1)::text || '/' || (2023 + ((g - 1) % 24) / 12)::text
                        ELSE lpad((((g - 1) % 12) + 1)::text, 2, '0') || '/' || (2023 + ((g - 1) % 24) / 12)::text
                   END,
                   CASE WHEN g % 10 = 0 THEN 'Fechamento | Consolidada no boleto único #' || (g + 1)::text || '.' END
            FROM generate_series(1, :n) g
        """), {'n': contratos * 24})
        conn.execute(text("""
            INSERT INTO billings (client_id, item_id, billing_type, amount, due_date, status, is_deleted,
                                  installment_number, installment_total, period_label)
            SELECT 1, (g - 1) / 3 + 1, 'item', 30, '2024-01-10', 'PAID', false, ((g - 1) % 3) + 1, 3, '01/2024'
            FROM generate_series(1, :n) g
        """), {'n': (N // 20) * 3})
        total = conn.execute(text('SELECT count(*) FROM billings')).scalar_one()
    print(f'semeadura: {total} cobranças, {contratos} contratos em {time.monotonic() - inicio:.1f}s')
    with engine.begin() as conn:
        conn.execute(text('ANALYZE'))

    codigo, dur, erro = alembic('upgrade', 'head')
    print(f'upgrade e5c2a9d71f04 (preflight + colunas + backfill + índices + CHECKs): exit={codigo} {dur:.1f}s {erro}')
    with engine.connect() as conn:
        preenchidas = conn.execute(text('SELECT count(*) FROM billings WHERE competencia IS NOT NULL')).scalar_one()
        vinculadas = conn.execute(text('SELECT count(*) FROM billings WHERE substituted_by_id IS NOT NULL')).scalar_one()
    print(f'backfill: competencia={preenchidas} substituted_by_id={vinculadas}')
    codigo, dur, erro = alembic('downgrade', 'd9e4f1a7b2c5')
    print(f'downgrade para d9e4f1a7b2c5: exit={codigo} {dur:.1f}s {erro}')
    codigo, dur, erro = alembic('upgrade', 'head')
    print(f'novo upgrade: exit={codigo} {dur:.1f}s {erro}')
finally:
    engine.dispose()
    with ADMIN.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{NOME}" WITH (FORCE)'))
