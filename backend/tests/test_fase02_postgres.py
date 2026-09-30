"""Fase 02 — corridas reais em PostgreSQL (duas sessões).

Contrato de concorrência (docs/financeiro/concorrencia.md): quem perde a
corrida recebe conflito de domínio (409) ou resultado idempotente — nunca 500
e nunca uma segunda obrigação efetiva.

Os triggers de atraso só alargam a janela entre a checagem da aplicação e o
INSERT; o que é observado é o seam HTTP.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import text

from app.models.billing import Billing
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus, ClientStatus
from app.models.plan import Plan
from tests.test_database_concurrency_postgres import postgres_api  # noqa: F401 — fixture

pytestmark = pytest.mark.postgres


def _atrasar_insert(engine, condicao: str, segundos: float = 0.4) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE FUNCTION aa_atraso_fase02() RETURNS trigger AS $$
            BEGIN
                IF {condicao} THEN
                    PERFORM pg_sleep({segundos});
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))
        conn.execute(text("""
            CREATE TRIGGER aa_atraso_fase02 BEFORE INSERT ON billings
            FOR EACH ROW EXECUTE FUNCTION aa_atraso_fase02()
        """))


def _contrato(sessions) -> int:
    with sessions() as db:
        cliente = Client(name='Cliente F02', cpf_cnpj='52998224725', type='pf', status=ClientStatus.ACTIVE)
        plano = Plan(name='Plano F02', price=Decimal('80.00'))
        db.add_all([cliente, plano])
        db.flush()
        contrato = Contract(client_id=cliente.id, plan_id=plano.id, start_date=date(2025, 1, 10),
                            status='ativo', billing_day=10)
        db.add(contrato)
        db.commit()
        return contrato.id


def _em_paralelo(*chamadas):
    barreira = Barrier(len(chamadas))

    def rodar(fn):
        barreira.wait()
        return fn()

    with ThreadPoolExecutor(max_workers=len(chamadas)) as pool:
        return [f.result() for f in [pool.submit(rodar, fn) for fn in chamadas]]


def test_parser_e_trigger_do_create_all_iguais_aos_da_migration(postgres_api):
    """O create_all dos testes precisa do mesmo trigger da migration, senão os
    testes validariam um banco diferente do de produção."""
    _, sessions, engine = postgres_api
    contrato_id = _contrato(sessions)
    with engine.begin() as conn:
        cliente_id = conn.execute(text('SELECT client_id FROM contracts WHERE id = :id'),
                                  {'id': contrato_id}).scalar_one()
        conn.execute(text(
            "INSERT INTO billings (client_id, contract_id, billing_type, amount, due_date, status, "
            "is_deleted, period_label) VALUES (:c, :k, 'recorrente', 80, '2099-09-10', 'PENDING', "
            "false, ' 9/2099')"
        ), {'c': cliente_id, 'k': contrato_id})
        assert conn.execute(text('SELECT competencia FROM billings')).scalar_one() == date(2099, 9, 1)


def test_duas_sessoes_mesmo_contrato_e_mes_com_rotulos_equivalentes(postgres_api):
    http, sessions, engine = postgres_api
    contrato_id = _contrato(sessions)
    with sessions() as db:
        cliente_id = db.get(Contract, contrato_id).client_id
    # As duas passam pela checagem da aplicação antes de qualquer INSERT
    # confirmar; só o índice pode decidir.
    _atrasar_insert(engine, "NEW.billing_type = 'recorrente'")

    def criar(rotulo):
        return lambda: http.post('/api/v1/billings/', json={
            'client_id': cliente_id, 'contract_id': contrato_id, 'amount': 80,
            'due_date': '2099-09-10', 'billing_type': 'recorrente', 'period_label': rotulo,
        })

    respostas = _em_paralelo(criar('09/2099'), criar('9/2099'))
    assert sorted(r.status_code for r in respostas) == [200, 409], [r.text for r in respostas]
    conflito = next(r for r in respostas if r.status_code == 409).json()['detail']
    assert 'já tem mensalidade lançada' in str(conflito)
    with sessions() as db:
        assert db.query(Billing).filter(Billing.contract_id == contrato_id).count() == 1


def test_carne_e_lancamento_manual_concorrentes_no_mesmo_mes(postgres_api):
    http, sessions, engine = postgres_api
    contrato_id = _contrato(sessions)
    with sessions() as db:
        cliente_id = db.get(Contract, contrato_id).client_id
    _atrasar_insert(engine, "NEW.billing_type IN ('recorrente', 'carne')")

    respostas = _em_paralelo(
        lambda: http.post('/api/v1/billings/parcelar', json={
            'contract_id': contrato_id, 'num_parcelas': 2, 'primeiro_vencimento': '2099-09-10',
        }),
        lambda: http.post('/api/v1/billings/', json={
            'client_id': cliente_id, 'contract_id': contrato_id, 'amount': 80,
            'due_date': '2099-09-10', 'billing_type': 'recorrente', 'period_label': '9/2099',
        }),
    )
    assert all(r.status_code in (200, 409) for r in respostas), [r.text for r in respostas]
    assert [r.status_code for r in respostas].count(200) == 1
    with sessions() as db:
        setembro = db.query(Billing).filter(
            Billing.contract_id == contrato_id,
            Billing.competencia == date(2099, 9, 1),
            Billing.is_deleted.is_(False),
        ).count()
    assert setembro == 1


def test_fechamentos_de_meses_diferentes_nao_duplicam_servico_avulso(postgres_api):
    """Reprodução da auditoria (cross_period_service_race): setembro e outubro
    selecionando o mesmo serviço sem contrato persistiam duas parcelas 1."""
    http, sessions, engine = postgres_api
    with sessions() as db:
        cliente = Client(name='Cliente Serviço', cpf_cnpj='11144477735', type='pf', status=ClientStatus.ACTIVE)
        db.add(cliente)
        db.flush()
        item = ClientChargeItem(client_id=cliente.id, title='Instalação', quantity=1,
                                unit_price=Decimal('100'), total_amount=Decimal('100'),
                                installment_count=1, start_date=date(2025, 9, 5))
        db.add(item)
        db.commit()
        item_id = item.id
    _atrasar_insert(engine, "NEW.billing_type = 'item'")

    def fechar(mes):
        return lambda: http.post('/api/v1/billing-closure/generate', params={
            'reference_month': mes, 'charge_item_ids': [item_id],
        })

    respostas = _em_paralelo(fechar('2025-09'), fechar('2025-10'))
    assert [r.status_code for r in respostas] == [200, 200], [r.text for r in respostas]
    gerados = sorted(r.json()['services_generated'] for r in respostas)
    assert gerados == [0, 1]
    perdedor = next(r.json() for r in respostas if r.json()['services_generated'] == 0)
    assert perdedor['services_already_billed'] == [item_id]
    with sessions() as db:
        parcelas = db.query(Billing).filter(Billing.item_id == item_id).all()
    assert len(parcelas) == 1
    assert parcelas[0].amount == Decimal('100.00')


def test_indice_de_parcela_barra_escritor_que_esquece_a_trava(postgres_api):
    """Barreira final: mesmo sem a trava do item, a segunda parcela 1 efetiva
    não entra."""
    _, sessions, engine = postgres_api
    with sessions() as db:
        cliente = Client(name='Cliente Índice', cpf_cnpj='39053344705', type='pf', status=ClientStatus.ACTIVE)
        db.add(cliente)
        db.flush()
        item = ClientChargeItem(client_id=cliente.id, title='Serviço', quantity=1,
                                unit_price=Decimal('50'), total_amount=Decimal('50'),
                                installment_count=1, start_date=date(2025, 9, 5))
        db.add(item)
        db.commit()
        ids = (cliente.id, item.id)

    def inserir():
        with sessions() as db:
            db.add(Billing(client_id=ids[0], item_id=ids[1], installment_number=1, installment_total=1,
                           amount=Decimal('50'), due_date=date(2025, 9, 5), billing_type='item',
                           status=BillingStatus.PENDING))
            try:
                db.commit()
                return 'ok'
            except Exception as exc:  # noqa: BLE001 — o teste classifica o erro abaixo
                return type(exc.orig).__name__

    resultados = _em_paralelo(inserir, inserir)
    assert sorted(resultados) == ['UniqueViolation', 'ok']
