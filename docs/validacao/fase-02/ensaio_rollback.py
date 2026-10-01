"""Ensaio de rollback da Fase 02 com o código antigo operando no meio.

Uma etapa por execução, cada uma com o código que estiver em /app:

    ENSAIO_DB=<banco> python ensaio_rollback.py preparar    # código NOVO
    ENSAIO_DB=<banco> python ensaio_rollback.py downgrade   # código NOVO
    ENSAIO_DB=<banco> python ensaio_rollback.py antigo      # código ANTIGO (71acf47)
    ENSAIO_DB=<banco> python ensaio_rollback.py reupgrade   # código NOVO

Todas usam o caminho real do boot (main._apply_database_migrations) e a API.
Só para PostgreSQL descartável (TEST_DATABASE_URL + ENSAIO_DB).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ETAPA = sys.argv[1]
URL = make_url(os.environ['TEST_DATABASE_URL']).set(database=os.environ['ENSAIO_DB'])
URL_TXT = URL.render_as_string(hide_password=False)
os.environ['DATABASE_URL'] = URL_TXT

if ETAPA == 'criar':
    admin = create_engine(make_url(os.environ['TEST_DATABASE_URL']), isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{os.environ["ENSAIO_DB"]}"'))
    sys.exit(0)
if ETAPA == 'apagar':
    admin = create_engine(make_url(os.environ['TEST_DATABASE_URL']), isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{os.environ["ENSAIO_DB"]}" WITH (FORCE)'))
    sys.exit(0)
if ETAPA == 'downgrade':
    r = subprocess.run([sys.executable, '-m', 'alembic', 'downgrade', 'd9e4f1a7b2c5'],
                       capture_output=True, text=True, cwd='/app')
    engine = create_engine(URL_TXT)
    with engine.connect() as conn:
        guardadas = conn.execute(text('SELECT billing_id FROM fase02_competencias_liberadas')).scalars().all() \
            if r.returncode == 0 else None
        versao = conn.execute(text('SELECT version_num FROM alembic_version')).scalar()
    print(json.dumps({'etapa': ETAPA, 'exit': r.returncode, 'versao': versao,
                      'liberadas_guardadas': guardadas, 'erro': r.stderr.splitlines()[-1:] if r.returncode else []},
                     ensure_ascii=False))
    sys.exit(r.returncode)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.main as main  # noqa: E402
from app.api.deps import get_current_user  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.models.billing import Billing  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.contract import Contract  # noqa: E402
from app.models.enums import ClientStatus, UserRole  # noqa: E402
from app.models.plan import Plan  # noqa: E402
from app.models.user import User  # noqa: E402

main._apply_database_migrations()  # o que o boot faria com este código
engine = create_engine(URL_TXT)
Sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _db():
    s = Sessions()
    try:
        yield s
    finally:
        s.close()


with Sessions() as s:
    admin = s.query(User).filter_by(email='ensaio@test.local').first()
    if not admin:
        admin = User(name='Ensaio', email='ensaio@test.local', role=UserRole.ADMIN, active=True,
                     is_deleted=False, password_hash='x')
        s.add(admin)
        s.commit()
        s.refresh(admin)
    s.expunge(admin)
main.app.dependency_overrides[get_db] = _db
main.app.dependency_overrides[get_current_user] = lambda: admin
http = TestClient(main.app, raise_server_exceptions=False)


def contrato(cpf: str) -> tuple[int, int]:
    with Sessions() as s:
        c = Client(name=f'Cliente {cpf}', cpf_cnpj=cpf, type='pf', status=ClientStatus.ACTIVE)
        p = Plan(name=f'Plano {cpf}', price=Decimal('80.00'))
        s.add_all([c, p])
        s.flush()
        k = Contract(client_id=c.id, plan_id=p.id, start_date=date(2025, 1, 10), status='ativo', billing_day=10)
        s.add(k)
        s.commit()
        return c.id, k.id


def mensalidade(cliente, k, rotulo, venc):
    r = http.post('/api/v1/billings/', json={'client_id': cliente, 'contract_id': k, 'amount': 80,
                                             'due_date': venc, 'billing_type': 'recorrente',
                                             'period_label': rotulo})
    assert r.status_code == 200, r.text
    return r.json()['id']


def estado():
    with engine.connect() as conn:
        cols = {r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'billings'"))}
        extra = ', competencia, competencia_liberada, substituted_by_id' if 'competencia' in cols else ''
        linhas = conn.execute(text(
            f'SELECT id, period_label, status::text, is_deleted{extra} FROM billings ORDER BY id')).fetchall()
        versao = conn.execute(text('SELECT version_num FROM alembic_version')).scalar()
    return versao, [list(map(str, linha)) for linha in linhas]


saida = {'etapa': ETAPA}
if ETAPA == 'preparar':
    cliente, k = contrato('52998224725')
    a = mensalidade(cliente, k, '09/2099', '2099-09-10')
    b = mensalidade(cliente, k, '10/2099', '2099-10-10')
    nova = http.post('/api/v1/billings/unificar', json={'billing_ids': [a, b], 'due_date': '2099-11-10'}).json()['id']
    c = mensalidade(cliente, k, '11/2099', '2099-11-10')
    cancel = http.post(f'/api/v1/billings/{c}/cancel', json={'reason': 'erro', 'liberar_competencia': True})
    saida.update(originais=[a, b], negociacao=nova, liberada=c, cancel_status=cancel.status_code)
elif ETAPA == 'antigo':
    # Código antigo operando: cria, carnê, negocia, cancela. Nada pode dar 500.
    cliente, k = contrato('11144477735')
    x = mensalidade(cliente, k, '9/2099', '2099-09-10')          # rótulo não canônico
    carne = http.post('/api/v1/billings/parcelar', json={'contract_id': k, 'num_parcelas': 2,
                                                        'primeiro_vencimento': '2099-10-10'})
    ids_carne = [p['id'] for p in carne.json()]
    uni = http.post('/api/v1/billings/unificar', json={'billing_ids': ids_carne, 'due_date': '2099-12-10'})
    canc = http.post(f'/api/v1/billings/{x}/cancel', json={'reason': 'teste rollback'})
    saida.update(status=[carne.status_code, uni.status_code, canc.status_code],
                 mensalidade_rotulo_9=x, carne=ids_carne, negociacao_antiga=uni.json().get('id'))
elif ETAPA == 'reupgrade':
    r = subprocess.run([sys.executable, '-m', 'alembic', 'check'], capture_output=True, text=True, cwd='/app')
    saida['alembic_check_exit'] = r.returncode
    # Depois de voltar: o mês 9/2099 do contrato criado pelo código antigo
    # continua ocupado (cancelada) — a competência foi calculada no upgrade.
    with Sessions() as s:
        k2 = s.query(Contract).order_by(Contract.id.desc()).first()
        cliente2 = k2.client_id
    dup = http.post('/api/v1/billings/', json={'client_id': cliente2, 'contract_id': k2.id, 'amount': 80,
                                               'due_date': '2099-09-10', 'billing_type': 'recorrente',
                                               'period_label': '09/2099'})
    saida['nova_mensalidade_no_mes_cancelado_pelo_codigo_antigo'] = dup.status_code
saida['versao'], saida['billings'] = estado()
print(json.dumps(saida, ensure_ascii=False))
