"""Provas antes/depois da Fase 02 — reproduz os cenários da auditoria.

Roda contra o código que estiver em /app (HEAD antigo ou branch da fase), com
um banco NOVO migrado pelo Alembic daquela versão (schema real, não
create_all). Usa só rotas/serviços que existem nas duas versões.

    TEST_DATABASE_URL=postgresql+psycopg://... python provas_fase02.py > saida.json

Só para PostgreSQL descartável: cria e apaga bancos no servidor apontado.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BASE_URL = make_url(os.environ['TEST_DATABASE_URL'])
ADMIN = create_engine(BASE_URL, isolation_level='AUTOCOMMIT')


def novo_banco() -> str:
    nome = f'prova_{uuid4().hex[:10]}'
    with ADMIN.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{nome}"'))
    return nome


def apagar_banco(nome: str) -> None:
    with ADMIN.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)'))


def url_de(nome: str) -> str:
    return BASE_URL.set(database=nome).render_as_string(hide_password=False)


def alembic(nome: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, DATABASE_URL=url_de(nome))
    return subprocess.run([sys.executable, '-m', 'alembic', *args], env=env,
                          capture_output=True, text=True, cwd='/app')


# O app é importado uma vez, apontando para o banco principal das provas.
PRINCIPAL = novo_banco()
os.environ['DATABASE_URL'] = url_de(PRINCIPAL)
r = alembic(PRINCIPAL, 'upgrade', 'head')
assert r.returncode == 0, r.stderr[-2000:]

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.deps import get_current_user  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.billing import Billing  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.client_charge_item import ClientChargeItem  # noqa: E402
from app.models.contract import Contract  # noqa: E402
from app.models.enums import BillingStatus, ClientStatus, UserRole  # noqa: E402
from app.models.plan import Plan  # noqa: E402
from app.models.user import User  # noqa: E402

ENGINE = create_engine(url_de(PRINCIPAL))
Sessions = sessionmaker(bind=ENGINE, autoflush=False, autocommit=False)


def _db():
    s = Sessions()
    try:
        yield s
    finally:
        s.close()


with Sessions() as s:
    admin = User(name='Admin prova', email='admin-prova@test.local', role=UserRole.ADMIN,
                 active=True, is_deleted=False, password_hash='x')
    s.add(admin)
    s.commit()
    s.refresh(admin)
    s.expunge(admin)
app.dependency_overrides[get_db] = _db
app.dependency_overrides[get_current_user] = lambda: admin
HTTP = TestClient(app, raise_server_exceptions=False)
_CPF = iter(['52998224725', '11144477735', '39053344705', '86288366757', '71428793860',
             '28625587887', '66671353004', '15350946056', '04416521062', '23548736090'])


def contrato(boleto_format=None) -> tuple[int, int]:
    with Sessions() as s:
        c = Client(name=f'Cliente {uuid4().hex[:6]}', cpf_cnpj=next(_CPF), type='pf',
                   status=ClientStatus.ACTIVE, boleto_format=boleto_format)
        p = Plan(name=f'Plano Prova {uuid4().hex[:6]}', price=Decimal('80.00'))
        s.add_all([c, p])
        s.flush()
        k = Contract(client_id=c.id, plan_id=p.id, start_date=date(2025, 1, 10), status='ativo', billing_day=10)
        s.add(k)
        s.commit()
        return c.id, k.id


def prova_rotulos_equivalentes():
    cliente, k = contrato()
    corpo = {'client_id': cliente, 'contract_id': k, 'amount': 80, 'due_date': '2099-09-10',
             'billing_type': 'recorrente'}
    a = HTTP.post('/api/v1/billings/', json={**corpo, 'period_label': '09/2099'})
    b = HTTP.post('/api/v1/billings/', json={**corpo, 'period_label': '9/2099'})
    with Sessions() as s:
        n = s.query(Billing).filter(Billing.contract_id == k, Billing.is_deleted.is_(False)).count()
    return {'status': [a.status_code, b.status_code], 'mensalidades_de_setembro_gravadas': n}


def prova_cancelada_e_carne():
    cliente, k = contrato()
    a = HTTP.post('/api/v1/billings/', json={'client_id': cliente, 'contract_id': k, 'amount': 80,
                                             'due_date': '2099-09-10', 'billing_type': 'recorrente',
                                             'period_label': '09/2099'}).json()
    HTTP.post(f"/api/v1/billings/{a['id']}/cancel", json={'reason': 'dispensa'})
    r = HTTP.post('/api/v1/billings/parcelar', json={'contract_id': k, 'num_parcelas': 2,
                                                     'primeiro_vencimento': '2099-09-10'})
    return {'status_carne': r.status_code, 'mensagem': r.json().get('detail')}


def prova_consolidado_removido():
    from app.services.billing_closure import execute_closure, simulate_closure

    cliente, _ = contrato(boleto_format='unico')
    with Sessions() as s:
        plano = s.query(Plan).first()
        s.add(Contract(client_id=cliente, plan_id=plano.id, start_date=date(2025, 1, 10),
                       status='ativo', billing_day=10))
        s.commit()
        res = execute_closure(s, date(2025, 5, 1), filter_type='client', client_id=cliente)
    unico = res['billing_ids'][0]
    d1 = HTTP.delete(f'/api/v1/billings/{unico}')
    d2 = HTTP.delete(f'/api/v1/billings/{unico}', params={'reverter_substituicao': True}) \
        if d1.status_code == 409 else None
    with Sessions() as s:
        sim = simulate_closure(s, date(2025, 5, 1), filter_type='client', client_id=cliente)
        abertas = s.query(Billing).filter(
            Billing.client_id == cliente, Billing.is_deleted.is_(False),
            Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE])).count()
    return {'delete_sem_confirmar': d1.status_code,
            'delete_revertendo': d2.status_code if d2 else None,
            'already_generated': [i['already_generated'] for i in sim['items']],
            'cobrancas_em_aberto_depois': abertas}


def prova_parcela_negativa():
    from app.services.financial import generate_item_billings

    cliente, _ = contrato()
    r = HTTP.post('/api/v1/client-charge-items/', json={
        'client_id': cliente, 'title': 'Taxa', 'unit_price': 0.02, 'quantity': 1,
        'installment_count': 4, 'start_date': '2099-01-10'})
    valores = None
    if r.status_code == 200:
        with Sessions() as s:
            item = s.get(ClientChargeItem, r.json()['id'])
            valores = [str(b.amount) for b in generate_item_billings(s, item)]
    return {'status_criacao': r.status_code, 'parcelas_geradas': valores}


def prova_corrida_entre_meses():
    with Sessions() as s:
        c = Client(name='Cliente corrida', cpf_cnpj=next(_CPF), type='pf', status=ClientStatus.ACTIVE)
        s.add(c)
        s.flush()
        item = ClientChargeItem(client_id=c.id, title='Serviço', quantity=1, unit_price=Decimal('100'),
                                total_amount=Decimal('100'), installment_count=1,
                                start_date=date(2025, 9, 5))
        s.add(item)
        s.commit()
        item_id = item.id
    with ENGINE.begin() as conn:
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION prova_atraso() RETURNS trigger AS $$
            BEGIN IF NEW.billing_type = 'item' THEN PERFORM pg_sleep(0.5); END IF; RETURN NEW; END;
            $$ LANGUAGE plpgsql"""))
        conn.execute(text('CREATE TRIGGER aa_prova_atraso BEFORE INSERT ON billings '
                          'FOR EACH ROW EXECUTE FUNCTION prova_atraso()'))
    barreira = Barrier(2)

    def fechar(mes):
        barreira.wait()
        return HTTP.post('/api/v1/billing-closure/generate',
                         params={'reference_month': mes, 'charge_item_ids': [item_id]})

    try:
        with ThreadPoolExecutor(2) as pool:
            respostas = list(pool.map(fechar, ['2025-09', '2025-10']))
    finally:
        with ENGINE.begin() as conn:
            conn.execute(text('DROP TRIGGER aa_prova_atraso ON billings'))
    with Sessions() as s:
        parcelas = s.query(Billing).filter(Billing.item_id == item_id).count()
    return {'status': [r.status_code for r in respostas], 'parcelas_1_gravadas': parcelas}


def prova_drift_alembic():
    nome = novo_banco()
    try:
        alembic(nome, 'upgrade', 'head')
        r = alembic(nome, 'check')
        remocoes = r.stderr.count("'remove_index'")
        return {'exit_code': r.returncode, 'remove_index_propostos': remocoes,
                'saida': (r.stdout + r.stderr).strip().splitlines()[-1][:300]}
    finally:
        apagar_banco(nome)


def prova_ciclo_down_up():
    nome = novo_banco()
    try:
        passos = []
        for args in (('upgrade', 'head'), ('downgrade', 'base'), ('upgrade', 'head')):
            r = alembic(nome, *args)
            erro = [l for l in r.stderr.splitlines() if 'Error' in l or 'already exists' in l]
            passos.append({'comando': ' '.join(args), 'exit_code': r.returncode,
                           'erro': erro[-1][:200] if erro else None})
        return passos
    finally:
        apagar_banco(nome)


def prova_carimbo_legado_parcial():
    """Banco pré-Alembic na baseline, sem uma coluna: o boot carimba?"""
    import app.main as main

    nome = novo_banco()
    eng = create_engine(url_de(nome))
    try:
        alembic(nome, 'upgrade', '96f61a589162')
        with eng.begin() as conn:
            conn.execute(text('DROP TABLE alembic_version'))
            conn.execute(text('ALTER TABLE billings DROP COLUMN period_label'))
        from app.core.config import settings

        original = main.engine
        main.engine = eng
        # alembic/env.py lê settings.database_url a cada comando.
        settings.database_url = url_de(nome)
        try:
            main._apply_database_migrations()
            resultado = 'carimbou e migrou sem reclamar da coluna faltando'
        except Exception as exc:  # noqa: BLE001 — a prova registra qualquer desfecho
            resultado = f'exceção: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}'
        finally:
            main.engine = original
            settings.database_url = url_de(PRINCIPAL)
        with eng.connect() as conn:
            existe = conn.execute(text("SELECT to_regclass('alembic_version') IS NOT NULL")).scalar()
            versao = conn.execute(text('SELECT version_num FROM alembic_version')).scalar() if existe else None
        return {
            'resultado': resultado,
            'alembic_version_gravada': versao,
            'leitura': ('carimbou o banco parcial e só quebrou numa migration adiante'
                        if versao else 'recusou antes de carimbar; banco intocado'),
        }
    finally:
        eng.dispose()
        apagar_banco(nome)


resultados = {}
for nome_prova, fn in [
    ('FIN-04 rotulos_equivalentes', prova_rotulos_equivalentes),
    ('FIN-01 cancelada_e_carne', prova_cancelada_e_carne),
    ('FIN-01 consolidado_removido', prova_consolidado_removido),
    ('FIN-11 parcela_negativa', prova_parcela_negativa),
    ('FIN-12 corrida_entre_meses', prova_corrida_entre_meses),
    ('DB-01 drift_alembic', prova_drift_alembic),
    ('DB-04 ciclo_down_up', prova_ciclo_down_up),
    ('DB-03 carimbo_legado_parcial', prova_carimbo_legado_parcial),
]:
    try:
        resultados[nome_prova] = fn()
    except Exception:  # noqa: BLE001 — a prova registra a falha em vez de parar
        resultados[nome_prova] = {'erro': traceback.format_exc()[-800:]}

ENGINE.dispose()
apagar_banco(PRINCIPAL)
print(json.dumps(resultados, indent=1, ensure_ascii=False, default=str))
