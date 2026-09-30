"""Ensaio de rollback da Fase 03 com o código antigo operando no meio.

Uma etapa por execução, cada uma com o código que estiver em /app:

    ENSAIO_DB=<banco> python ensaio_rollback.py preparar    # código NOVO
    ENSAIO_DB=<banco> python ensaio_rollback.py downgrade   # código NOVO
    ENSAIO_DB=<banco> python ensaio_rollback.py antigo      # código ANTIGO (44733f0)
    ENSAIO_DB=<banco> python ensaio_rollback.py reupgrade   # código NOVO

Usa o caminho real do boot (main._apply_database_migrations) e a API. Nenhuma
chamada à Ailos: títulos e desfechos são linhas sintéticas. Só para
PostgreSQL descartável (TEST_DATABASE_URL + ENSAIO_DB).
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
FASE02 = 'e5c2a9d71f04'
URL = make_url(os.environ['TEST_DATABASE_URL']).set(database=os.environ['ENSAIO_DB'])
URL_TXT = URL.render_as_string(hide_password=False)
os.environ['DATABASE_URL'] = URL_TXT

if ETAPA in ('criar', 'apagar'):
    admin = create_engine(make_url(os.environ['TEST_DATABASE_URL']), isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:
        if ETAPA == 'criar':
            conn.execute(text(f'CREATE DATABASE "{os.environ["ENSAIO_DB"]}"'))
        else:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{os.environ["ENSAIO_DB"]}" WITH (FORCE)'))
    sys.exit(0)


def estado_boletos(engine) -> list[list[str]]:
    with engine.connect() as conn:
        cols = {r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'ailos_boletos'"))}
        extra = ', baixa_status, pendencia' if 'baixa_status' in cols else ''
        linhas = conn.execute(text(
            f'SELECT a.billing_id, b.status::text, a.status_ailos{extra} '
            'FROM ailos_boletos a JOIN billings b ON b.id = a.billing_id ORDER BY a.billing_id')).fetchall()
    return [list(map(str, linha)) for linha in linhas]


def tabelas(engine) -> dict:
    with engine.connect() as conn:
        nomes = conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND "
            "(tablename LIKE 'fase03_%' OR tablename IN ('billing_adjustments', 'payable_change_logs', "
            "'cnab_remessas', 'cnab_remessa_itens')) ORDER BY 1")).scalars().all()
        return {n: conn.execute(text(f'SELECT count(*) FROM {n}')).scalar() for n in nomes}


if ETAPA == 'downgrade':
    r = subprocess.run([sys.executable, '-m', 'alembic', 'downgrade', FASE02],
                       capture_output=True, text=True, cwd='/app')
    engine = create_engine(URL_TXT)
    with engine.connect() as conn:
        versao = conn.execute(text('SELECT version_num FROM alembic_version')).scalar()
    print(json.dumps({'etapa': ETAPA, 'exit': r.returncode, 'versao': versao, 'tabelas': tabelas(engine),
                      'boletos': estado_boletos(engine),
                      'erro': r.stderr.splitlines()[-3:] if r.returncode else []}, ensure_ascii=False))
    sys.exit(r.returncode)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.main as main  # noqa: E402
from app.api.deps import get_current_user  # noqa: E402
from app.db.session import get_db  # noqa: E402
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


def avulsa(cliente, k, venc='2099-11-10') -> int:
    r = http.post('/api/v1/billings/', json={'client_id': cliente, 'contract_id': k, 'amount': 80,
                                             'due_date': venc, 'billing_type': 'avulsa'})
    assert r.status_code == 200, r.text
    return r.json()['id']


def titulo(billing_id: int, *, status='0', registrado=True) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            'INSERT INTO ailos_boletos (billing_id, numero_convenio, nosso_numero, linha_digitavel, '
            'codigo_barras, status_ailos) VALUES (:b, \'102004\', :nn, :ld, :cb, :st)'
        ), {'b': billing_id, 'st': status, 'nn': f'NN{billing_id}' if registrado else None,
            'ld': f'LD{billing_id}' if registrado else None, 'cb': f'CB{billing_id}' if registrado else None})


saida = {'etapa': ETAPA}
if ETAPA == 'preparar':
    cliente, k = contrato('52998224725')
    cancelada = avulsa(cliente, k)
    titulo(cancelada)
    c1 = http.post(f'/api/v1/billings/{cancelada}/cancel', json={'reason': 'desistiu', 'confirmar_boleto_ailos': True})
    incerta = avulsa(cliente, k)
    titulo(incerta, status='DESFECHO_DESCONHECIDO', registrado=False)
    com_desconto = avulsa(cliente, k)
    rec = http.post(f'/api/v1/billings/{com_desconto}/receive', json={
        'paid_amount': 70, 'payment_date': '2099-11-01', 'payment_method': 'pix',
        'tratamento_diferenca': 'desconto', 'justificativa_diferenca': 'acordo'})
    divergente = avulsa(cliente, k)
    titulo(divergente)
    with engine.begin() as conn:
        conn.execute(text("UPDATE ailos_boletos SET pendencia = 'pagamento_divergente' WHERE billing_id = :b"),
                     {'b': divergente})
    pid = http.post('/api/v1/payables/', json={'description': 'Aluguel', 'amount': 500, 'due_date': '2099-10-10'}).json()['id']
    pay = http.post(f'/api/v1/payables/{pid}/pay', json={'payment_date': '2099-10-10', 'payment_method': 'pix'})
    saida.update(cancelada=cancelada, incerta=incerta, com_desconto=com_desconto, divergente=divergente,
                 status=[c1.status_code, rec.status_code, pay.status_code], tabelas=tabelas(engine))
elif ETAPA == 'antigo':
    # Código antigo operando sobre o banco rebaixado. Nada pode dar 500 e a
    # cobrança ex-"desfecho desconhecido" (agora PROCESSANDO) segue protegida.
    with engine.connect() as conn:
        incerta, divergente = conn.execute(text(
            "SELECT min(billing_id) FILTER (WHERE status_ailos = 'PROCESSANDO'), "
            "max(billing_id) FILTER (WHERE status_ailos = '0') FROM ailos_boletos")).one()
    put_incerta = http.put(f'/api/v1/billings/{incerta}', json={'amount': 99, 'justification': 'x'})
    cancel_incerta = http.post(f'/api/v1/billings/{incerta}/cancel', json={'reason': 'x'})
    # Recebe por fora a cobrança que tinha título ativo (o código antigo deixa).
    rec = http.post(f'/api/v1/billings/{divergente}/receive', json={
        'paid_amount': 80, 'payment_date': '2099-11-02', 'payment_method': 'dinheiro'})
    cliente, k = contrato('11144477735')
    nova = avulsa(cliente, k)
    canc = http.post(f'/api/v1/billings/{nova}/cancel', json={'reason': 'teste rollback'})
    saida.update(status={'put_incerta': put_incerta.status_code, 'cancel_incerta': cancel_incerta.status_code,
                         'receber_divergente': rec.status_code, 'criar': 200, 'cancelar_nova': canc.status_code},
                 tabelas=tabelas(engine))
elif ETAPA == 'reupgrade':
    r = subprocess.run([sys.executable, '-m', 'alembic', 'check'], capture_output=True, text=True, cwd='/app')
    saida['alembic_check_exit'] = r.returncode
    with engine.connect() as conn:
        saida['ajustes'] = conn.execute(text('SELECT kind, amount::text FROM billing_adjustments')).all()
        saida['ajustes'] = [list(map(str, a)) for a in saida['ajustes']]
        saida['historico_contas_a_pagar'] = conn.execute(text('SELECT count(*) FROM payable_change_logs')).scalar()
        incerta = conn.execute(text(
            "SELECT billing_id FROM ailos_boletos WHERE status_ailos = 'DESFECHO_DESCONHECIDO'")).scalar()
    saida['put_incerta_depois'] = http.put(
        f'/api/v1/billings/{incerta}', json={'amount': 99, 'justification': 'x'}).status_code if incerta else None
    saida['tabelas'] = tabelas(engine)
with engine.connect() as conn:
    saida['versao'] = conn.execute(text('SELECT version_num FROM alembic_version')).scalar()
saida['boletos'] = estado_boletos(engine)
print(json.dumps(saida, ensure_ascii=False, default=str))
