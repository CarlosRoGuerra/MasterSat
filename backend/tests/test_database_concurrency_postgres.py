from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_current_user
from app.db.session import Base, get_db
from app.main import app
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus, ClientStatus, UserRole
from app.models.plan import Plan
from app.models.user import User


pytestmark = pytest.mark.postgres


@pytest.fixture()
def postgres_api():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL não configurada")

    schema = f"test_mastersat_{uuid4().hex}"
    admin_engine = create_engine(url, future=True)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_engine(
        url,
        future=True,
        connect_args={
            "options": f"-csearch_path={schema}",
            "application_name": schema,
        },
    )
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    def override_db():
        session = sessions()
        try:
            yield session
        finally:
            session.close()

    admin = User(
        name="Admin concorrência",
        email="admin-concorrencia@test.local",
        role=UserRole.ADMIN,
        active=True,
        is_deleted=False,
        password_hash="not-a-real-hash",
    )
    with sessions() as session:
        session.add(admin)
        session.commit()
        session.refresh(admin)
        session.expunge(admin)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: admin
    client = TestClient(app, raise_server_exceptions=False)

    try:
        yield client, sessions, engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
        if not schema.startswith("test_mastersat_"):
            raise RuntimeError("Recusa de remover schema fora do prefixo de teste")
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def test_simultaneous_unification_has_one_effect(postgres_api):
    http, sessions, engine = postgres_api
    with sessions() as db:
        client = Client(
            name="Cliente concorrente",
            cpf_cnpj="52998224725",
            type="pf",
            status=ClientStatus.ACTIVE,
        )
        db.add(client)
        db.flush()
        originals = [
            Billing(
                client_id=client.id,
                amount=Decimal("10.10"),
                due_date=date(2099, 1, day),
                status=BillingStatus.PENDING,
                billing_type="recorrente",
                title=f"Original {day}",
            )
            for day in (10, 11)
        ]
        db.add_all(originals)
        db.commit()
        original_ids = [billing.id for billing in originals]

    # Faz as duas requisições atravessarem a validação antiga antes de qualquer
    # INSERT da unificação ser confirmado. O teste continua observando somente
    # o seam HTTP; o trigger é apenas uma barreira no banco de teste.
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE FUNCTION delay_unified_billing() RETURNS trigger AS $$
            BEGIN
                IF NEW.billing_type = 'avulsa' THEN
                    PERFORM pg_sleep(0.35);
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))
        connection.execute(text("""
            CREATE TRIGGER delay_unified_billing_insert
            BEFORE INSERT ON billings
            FOR EACH ROW EXECUTE FUNCTION delay_unified_billing()
        """))

    barrier = Barrier(2)

    def unify():
        barrier.wait()
        return http.post(
            "/api/v1/billings/unificar",
            json={"billing_ids": original_ids, "due_date": "2099-02-10"},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: unify(), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 400]
    rows = http.get("/api/v1/billings/")
    assert rows.status_code == 200
    unified = [row for row in rows.json() if row["billing_type"] == "avulsa"]
    assert len(unified) == 1
    for billing_id in original_ids:
        original = http.get(f"/api/v1/billings/{billing_id}")
        assert original.status_code == 200
        assert original.json()["status"] == "cancelada"


def _create_open_billing(sessions, *, cpf_cnpj: str, amount: str = "10.10") -> int:
    with sessions() as db:
        client = Client(
            name=f"Cliente {cpf_cnpj}",
            cpf_cnpj=cpf_cnpj,
            type="pf",
            status=ClientStatus.ACTIVE,
        )
        db.add(client)
        db.flush()
        billing = Billing(
            client_id=client.id,
            amount=Decimal(amount),
            due_date=date(2099, 3, 10),
            status=BillingStatus.PENDING,
            billing_type="recorrente",
            title="Cobrança concorrente",
        )
        db.add(billing)
        db.commit()
        return billing.id


def _delay_every_billing_update(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE FUNCTION delay_billing_update() RETURNS trigger AS $$
            BEGIN
                PERFORM pg_sleep(0.35);
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))
        connection.execute(text("""
            CREATE TRIGGER delay_billing_update_trigger
            BEFORE UPDATE ON billings
            FOR EACH ROW EXECUTE FUNCTION delay_billing_update()
        """))


def test_simultaneous_receive_and_cancel_have_one_terminal_transition(postgres_api):
    http, sessions, engine = postgres_api
    billing_id = _create_open_billing(sessions, cpf_cnpj="12345678909")
    _delay_every_billing_update(engine)
    barrier = Barrier(2)

    def receive():
        barrier.wait()
        return http.post(
            "/api/v1/billings/lote/situacao",
            json={
                "billing_ids": [billing_id],
                "action": "receber",
                "payment_date": "2026-08-27",
                "payment_method": "pix",
            },
        )

    def cancel():
        barrier.wait()
        return http.post(
            "/api/v1/billings/lote/situacao",
            json={
                "billing_ids": [billing_id],
                "action": "cancelar",
                "reason": "cancelamento concorrente",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receive_future = executor.submit(receive)
        cancel_future = executor.submit(cancel)
        responses = [receive_future.result(), cancel_future.result()]

    assert all(response.status_code == 200 for response in responses), [
        (response.status_code, response.text) for response in responses
    ]
    assert sum(billing_id in response.json()["processados"] for response in responses) == 1
    assert sum(billing_id in response.json()["ignorados"] for response in responses) == 1

    current = http.get(f"/api/v1/billings/{billing_id}")
    assert current.status_code == 200
    body = current.json()
    assert body["status"] in {"paga", "cancelada"}
    if body["status"] == "cancelada":
        assert body["payment_date"] is None
        assert body["paid_amount"] is None


def test_maintenance_racing_receive_never_mixes_amounts(postgres_api):
    http, sessions, engine = postgres_api
    billing_id = _create_open_billing(
        sessions,
        cpf_cnpj="11144477735",
        amount="10.10",
    )
    _delay_every_billing_update(engine)
    barrier = Barrier(2)

    def receive():
        barrier.wait()
        return http.post(
            "/api/v1/billings/lote/situacao",
            json={
                "billing_ids": [billing_id],
                "action": "receber",
                "payment_date": "2026-08-27",
                "payment_method": "pix",
            },
        )

    def maintain():
        barrier.wait()
        return http.post(
            "/api/v1/billings/lote/manutencao",
            json={
                "billing_ids": [billing_id],
                "amount": 20.20,
                "justification": "ajuste concorrente",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receive_future = executor.submit(receive)
        maintain_future = executor.submit(maintain)
        responses = [receive_future.result(), maintain_future.result()]

    assert all(response.status_code == 200 for response in responses), [
        (response.status_code, response.text) for response in responses
    ]
    current = http.get(f"/api/v1/billings/{billing_id}")
    assert current.status_code == 200
    body = current.json()
    assert body["status"] == "paga"
    assert body["amount"] == body["paid_amount"]


def test_concurrent_duplicate_client_returns_specific_conflict(postgres_api):
    http, _, engine = postgres_api
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE FUNCTION delay_client_insert() RETURNS trigger AS $$
            BEGIN
                PERFORM pg_sleep(0.35);
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))
        connection.execute(text("""
            CREATE TRIGGER delay_client_insert_trigger
            BEFORE INSERT ON clients
            FOR EACH ROW EXECUTE FUNCTION delay_client_insert()
        """))

    payload = {
        "name": "Cliente duplicado concorrente",
        "cpf_cnpj": "39053344705",
        "type": "pf",
        "status": "ativo",
    }
    barrier = Barrier(2)

    def create_duplicate():
        barrier.wait()
        return http.post("/api/v1/clients/", json=payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: create_duplicate(), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["detail"] == "Já existe cliente com este CPF/CNPJ"

    # A sessão da requisição que perdeu a corrida precisa ter sido revertida;
    # uma nova escrita válida continua funcionando normalmente.
    valid = http.post(
        "/api/v1/clients/",
        json={**payload, "name": "Cliente após rollback", "cpf_cnpj": "16899535009"},
    )
    assert valid.status_code == 200

    listed = http.get(
        "/api/v1/clients/",
        params={"cpf_cnpj": payload["cpf_cnpj"]},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


def test_concurrent_case_insensitive_email_returns_specific_conflict(postgres_api):
    http, _, engine = postgres_api
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE FUNCTION delay_user_insert() RETURNS trigger AS $$
            BEGIN
                PERFORM pg_sleep(0.35);
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))
        connection.execute(text("""
            CREATE TRIGGER delay_user_insert_trigger
            BEFORE INSERT ON users
            FOR EACH ROW EXECUTE FUNCTION delay_user_insert()
        """))

    barrier = Barrier(2)

    def create_user(email: str):
        barrier.wait()
        return http.post(
            "/api/v1/users/",
            json={
                "name": "Usuário concorrente",
                "email": email,
                "role": "operacional",
                "active": True,
                "password": "Senha@123",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(
            create_user,
            ["Case.User@Test.Local", "case.user@test.local"],
        ))

    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["detail"] == "E-mail já cadastrado"


def test_concurrent_duplicate_plate_returns_specific_conflict(postgres_api):
    http, _, engine = postgres_api
    owner = http.post(
        "/api/v1/clients/",
        json={
            "name": "Proprietário dos veículos",
            "cpf_cnpj": "86288366703",
            "type": "pf",
            "status": "ativo",
        },
    )
    assert owner.status_code == 200

    with engine.begin() as connection:
        connection.execute(text("""
            CREATE FUNCTION delay_vehicle_insert() RETURNS trigger AS $$
            BEGIN
                PERFORM pg_sleep(0.35);
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))
        connection.execute(text("""
            CREATE TRIGGER delay_vehicle_insert_trigger
            BEFORE INSERT ON vehicles
            FOR EACH ROW EXECUTE FUNCTION delay_vehicle_insert()
        """))

    payload = {
        "client_id": owner.json()["id"],
        "plate": "RAC2E26",
        "type": "passeio",
    }
    barrier = Barrier(2)

    def create_duplicate():
        barrier.wait()
        return http.post("/api/v1/vehicles/", json=payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: create_duplicate(), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["detail"] == "Já existe veículo com essa placa"


def test_concurrent_duplicate_imei_returns_specific_conflict(postgres_api):
    http, _, engine = postgres_api
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE FUNCTION delay_tracker_insert() RETURNS trigger AS $$
            BEGIN
                PERFORM pg_sleep(0.35);
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))
        connection.execute(text("""
            CREATE TRIGGER delay_tracker_insert_trigger
            BEFORE INSERT ON trackers
            FOR EACH ROW EXECUTE FUNCTION delay_tracker_insert()
        """))

    payload = {
        "imei": "359339075555551",
        "brand": "Concorrente",
        "model": "C1",
    }
    barrier = Barrier(2)

    def create_duplicate():
        barrier.wait()
        return http.post("/api/v1/trackers/", json=payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: create_duplicate(), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["detail"] == "Já existe rastreador com este IMEI/ID"


def test_concurrent_tracker_batches_preserve_non_conflicting_items(postgres_api):
    http, _, engine = postgres_api
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE FUNCTION delay_batch_tracker_insert() RETURNS trigger AS $$
            BEGIN
                PERFORM pg_sleep(0.2);
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))
        connection.execute(text("""
            CREATE TRIGGER delay_batch_tracker_insert_trigger
            BEFORE INSERT ON trackers
            FOR EACH ROW EXECUTE FUNCTION delay_batch_tracker_insert()
        """))

    shared = "359339075555552"
    barrier = Barrier(2)

    def create_batch(unique_imei: str):
        barrier.wait()
        return http.post(
            "/api/v1/trackers/lote",
            json={
                "imeis": [shared, unique_imei],
                "brand": "Lote concorrente",
                "model": "C2",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(
            create_batch,
            ["359339075555553", "359339075555554"],
        ))

    assert [response.status_code for response in responses] == [200, 200]
    shared_results = [
        item
        for response in responses
        for item in response.json()["itens"]
        if item["imei"] == shared
    ]
    assert sorted(item["situacao"] for item in shared_results) == ["criado", "ja_existe"]
    assert sum(response.json()["criados"] for response in responses) == 3

    listed = http.get("/api/v1/trackers/", params={"search": "35933907555555"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 3


def test_ailos_registration_serializes_financial_maintenance(postgres_api):
    http, sessions, _ = postgres_api
    billing_id = _create_open_billing(
        sessions,
        cpf_cnpj="15350946056",
        amount="99.90",
    )
    with sessions() as db:
        billing = db.get(Billing, billing_id)
        client = db.get(Client, billing.client_id)
        client.zip_code = "28970-000"
        client.address_line = "Rua Principal"
        client.address_number = "100"
        client.neighborhood = "Centro"
        client.city = "Araruama"
        client.state = "RJ"
        db.commit()

    external_call_started = Event()
    allow_external_response = Event()

    class AilosResponse:
        json = {
            "documento": {
                "numeroDocumento": billing_id,
                "nossoNumero": "12345678",
                "identificadorUnicoTitulo": "ID-12345678",
            },
            "codigoBarras": {
                "codigoBarras": "CODIGO-BARRAS",
                "linhaDigitavel": "LINHA-DIGITAVEL",
            },
            "indicadorSituacaoBoleto": "REGISTRADO",
            "valorBoleto": {"valorNominal": 99.9},
            "vencimento": {"dataVencimento": "2099-03-10"},
        }

    def delayed_ailos_request(*args, **kwargs):
        external_call_started.set()
        assert allow_external_response.wait(timeout=5)
        return AilosResponse()

    with patch(
        "app.services.ailos_boletos.ailos_client.request",
        side_effect=delayed_ailos_request,
    ):
        with ThreadPoolExecutor(max_workers=2) as executor:
            register_future = executor.submit(
                http.post,
                "/api/v1/ailos/boletos",
                json={"billing_id": billing_id},
            )
            assert external_call_started.wait(timeout=5)
            maintenance_future = executor.submit(
                http.post,
                "/api/v1/billings/lote/manutencao",
                json={
                    "billing_ids": [billing_id],
                    "amount": 120.0,
                    "justification": "ajuste durante registro Ailos",
                },
            )
            # A reserva idempotente é confirmada antes da chamada externa:
            # a manutenção enxerga o estado em voo e falha sem precisar
            # aguardar a rede/banco responder.
            maintenance = maintenance_future.result(timeout=5)
            allow_external_response.set()
            registered = register_future.result(timeout=5)

    assert registered.status_code == 200
    assert maintenance.status_code == 409
    assert maintenance.json()["detail"]["code"] == "boleto_ailos_registrado"

    current = http.get(f"/api/v1/billings/{billing_id}")
    assert current.status_code == 200
    assert current.json()["amount"] == 99.9


def test_contract_delete_wins_race_before_closure_mutates(postgres_api):
    http, sessions, engine = postgres_api
    with sessions() as db:
        client = Client(
            name="Cliente fechamento concorrente",
            cpf_cnpj="39053344705",
            type="pf",
            status=ClientStatus.ACTIVE,
        )
        plan = Plan(
            name="Plano fechamento concorrente",
            price=Decimal("89.90"),
            active=True,
            billing_interval_months=1,
        )
        db.add_all([client, plan])
        db.flush()
        contract = Contract(
            client_id=client.id,
            plan_id=plan.id,
            start_date=date(2099, 4, 1),
            status="ativo",
            billing_day=15,
        )
        db.add(contract)
        db.commit()
        contract_id = contract.id

    with engine.begin() as connection:
        connection.execute(text("""
            CREATE FUNCTION delay_contract_soft_delete() RETURNS trigger AS $$
            BEGIN
                IF OLD.is_deleted = false AND NEW.is_deleted = true THEN
                    PERFORM pg_sleep(0.5);
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))
        connection.execute(text("""
            CREATE TRIGGER delay_contract_soft_delete_trigger
            BEFORE UPDATE ON contracts
            FOR EACH ROW EXECUTE FUNCTION delay_contract_soft_delete()
        """))

    with engine.connect() as connection:
        application_name = connection.execute(
            text("SELECT current_setting('application_name')")
        ).scalar_one()
        connection.rollback()

    def delete_contract():
        return http.delete(f"/api/v1/contracts/{contract_id}")

    def wait_until_delete_is_updating() -> None:
        deadline = time.monotonic() + 5
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            while time.monotonic() < deadline:
                active = connection.execute(
                    text("""
                        SELECT EXISTS (
                            SELECT 1
                            FROM pg_stat_activity
                            WHERE application_name = :application_name
                              AND pid <> pg_backend_pid()
                              AND state = 'active'
                              AND query ILIKE 'UPDATE%contracts%'
                        )
                    """),
                    {"application_name": application_name},
                ).scalar_one()
                if active:
                    return
                time.sleep(0.01)
        raise AssertionError("A exclusão não alcançou o UPDATE do contrato")

    with ThreadPoolExecutor(max_workers=2) as executor:
        delete_future = executor.submit(delete_contract)
        wait_until_delete_is_updating()
        closure_future = executor.submit(
            http.post,
            "/api/v1/billing-closure/generate",
            params={"reference_month": "2099-04"},
        )
        deleted = delete_future.result(timeout=5)
        closure = closure_future.result(timeout=5)

    assert deleted.status_code == 200
    assert closure.status_code == 409
    assert f"Contrato #{contract_id} foi removido durante o fechamento" in closure.json()["detail"]
    with sessions() as db:
        assert db.query(Billing).filter(Billing.contract_id == contract_id).count() == 0
