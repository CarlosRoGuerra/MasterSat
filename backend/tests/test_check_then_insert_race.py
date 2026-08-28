"""
Reproduz a corrida check-then-insert identificada em:

  - ailos_boletos.py  (`_consultar_carne_por_boleto` -> `_upsert_ailos_boleto`)
  - nfse_lote.py       (`criar_lote`, mesmo padrão usado por `nfse_nacional.emitir_nfse`)

Ambas seguem o formato:

    registro = db.query(Modelo).filter_by(billing_id=billing_id).first()
    if registro is None:
        registro = Modelo(billing_id=billing_id)
        db.add(registro)
    ...
    db.commit()

Sem lock, duas sessões que fazem o SELECT antes de qualquer COMMIT enxergam
"não existe" e as duas tentam o INSERT. `ailos_boletos.billing_id` e
`nfse_notas.billing_id` têm UNIQUE no schema (ver app/models/ailos_boleto.py e
app/models/nfse_nota.py), então a duplicata nunca chegava a existir no banco —
mas o segundo INSERT estourava IntegrityError sem tratamento nesses 3 pontos
(o terceiro é `nfse_nacional.emitir_nfse`, mesmo padrão), virando erro 500
(Ailos, via HTTP) ou exceção crua (NFS-e).

Isso foi corrigido nos 3 pontos: o INSERT roda dentro de um SAVEPOINT
(`Session.begin_nested()`); se o IntegrityError disparar, o savepoint desfaz
só aquela tentativa e o código reconsulta o registro que a sessão concorrente
já criou, reaproveitando-o em vez de propagar o erro. Estes testes agora
EXIGEM o resultado idempotente (200/200, ok/ok) — viram teste de regressão da
correção. Exigem Postgres de verdade (a corrida e o UNIQUE de schema não são
reproduzíveis no SQLite dos outros testes) — mesmo padrão de
test_database_concurrency_postgres.py.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_current_user
from app.db.session import Base, get_db
from app.main import app
from app.models.ailos_boleto import AilosBoleto
from app.models.ailos_lote import AilosLote
from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus, ClientStatus, UserRole
from app.models.nfse_nota import NfseNota
from app.models.user import User
from app.services import nfse_lote


pytestmark = pytest.mark.postgres


@pytest.fixture()
def postgres_api():
    """Cópia do fixture de test_database_concurrency_postgres.py — schema isolado
    por teste num Postgres real, para reproduzir UNIQUE constraint + corrida."""
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
        email="admin-concorrencia-race@test.local",
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


def _boleto_valido(billing_id: int) -> dict:
    return {
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


def test_carne_status_polling_races_on_ailos_boleto_insert(postgres_api):
    """Duas consultas simultâneas de status de um MESMO carnê (ex.: usuário com
    duas abas abertas, ou o polling do frontend sobrepondo uma chamada manual de
    "verificar novamente") chegam ao check-then-insert de `_upsert_ailos_boleto`
    ao mesmo tempo. Nenhuma trava o billing nem o carnê antes de inserir."""
    http, sessions, _engine = postgres_api

    with sessions() as db:
        client = Client(
            name="Cliente carnê concorrente",
            cpf_cnpj="52998224725",
            type="pf",
            status=ClientStatus.ACTIVE,
        )
        db.add(client)
        db.flush()
        billing = Billing(
            client_id=client.id,
            amount=Decimal("99.90"),
            due_date=date(2099, 3, 10),
            status=BillingStatus.PENDING,
            billing_type="carne",
            title="Parcela 1",
        )
        db.add(billing)
        db.flush()
        lote = AilosLote(
            tipo="carne",
            ticket="TICKET-RACE-1",
            numero_convenio="102004",
            billing_ids=[billing.id],
            status="processing",
        )
        db.add(lote)
        db.commit()
        billing_id = billing.id

    # As duas requisições só devem prosseguir depois que AMBAS já passaram pelo
    # SELECT "existe AilosBoleto?" (que dá None nas duas) — a barreira dentro do
    # mock da chamada externa garante a sobreposição sem depender de sorte de
    # timing entre threads.
    barrier = Barrier(2)

    def consultar_boleto_simultaneo(_db, _numero_boleto):
        barrier.wait(timeout=5)
        return _boleto_valido(billing_id)

    with patch(
        "app.services.ailos_boletos.consultar_boleto",
        side_effect=consultar_boleto_simultaneo,
    ):
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(
                lambda _: http.get("/api/v1/ailos/lotes/TICKET-RACE-1"),
                range(2),
            ))

    status_codes = sorted(response.status_code for response in responses)

    with sessions() as db:
        boletos = db.query(AilosBoleto).filter_by(billing_id=billing_id).all()

    # O UNIQUE em ailos_boletos.billing_id impede duplicata no banco, e
    # `_upsert_ailos_boleto` agora trata o IntegrityError do perdedor da
    # corrida (SAVEPOINT + reaproveita o registro vencedor) — as duas
    # respostas devem ser 200, idempotentes.
    assert len(boletos) == 1, (
        f"Esperado exatamente 1 AilosBoleto (UNIQUE deveria impedir duplicata), achou {len(boletos)}"
    )
    assert boletos[0].linha_digitavel == "LINHA-DIGITAVEL"
    assert status_codes == [200, 200], (
        f"Corrida ainda quebra o check-then-insert de _upsert_ailos_boleto: {status_codes}"
    )


def test_criar_lote_races_on_nfse_nota_insert(postgres_api):
    """Duas chamadas simultâneas a `criar_lote` para a MESMA cobrança (ex.: duplo
    clique em "confirmar emissão", ou duas abas) atravessam o check-then-insert
    de NfseNota em `nfse_lote.criar_lote` (linha ~172) ao mesmo tempo.

    Chama o serviço diretamente (não via HTTP) com `emitir_async=False` para não
    depender da thread daemon de emissão (que exige certificado ICP-Brasil) —
    o alvo aqui é só o check-then-insert de NfseNota, não a emissão em si.
    """
    _http, sessions, engine = postgres_api

    with sessions() as db:
        client = Client(
            name="Cliente NFS-e concorrente",
            cpf_cnpj="52998224725",
            type="pf",
            status=ClientStatus.ACTIVE,
            issue_invoice="sim",
        )
        db.add(client)
        db.flush()
        billing = Billing(
            client_id=client.id,
            amount=Decimal("150.00"),
            due_date=date(2099, 8, 10),
            status=BillingStatus.PENDING,
            billing_type="recorrente",
            title="Mensalidade",
            period_label="2099-08",
        )
        db.add(billing)
        db.commit()
        billing_id = billing.id

    # Alarga a janela da corrida no nível do banco (mesmo padrão dos triggers de
    # delay usados em test_database_concurrency_postgres.py): atrasa o INSERT
    # em nfse_notas para garantir que as duas sessões já tenham feito o SELECT
    # "existe nota?" (achando nenhuma) antes de qualquer COMMIT.
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE FUNCTION delay_nfse_nota_insert() RETURNS trigger AS $$
            BEGIN
                PERFORM pg_sleep(0.3);
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))
        connection.execute(text("""
            CREATE TRIGGER delay_nfse_nota_insert_trigger
            BEFORE INSERT ON nfse_notas
            FOR EACH ROW EXECUTE FUNCTION delay_nfse_nota_insert()
        """))

    barrier = Barrier(2)

    def chamar_criar_lote():
        barrier.wait(timeout=5)
        with sessions() as db:
            try:
                lote = nfse_lote.criar_lote(
                    db, "2099-08", [billing_id], emitir_async=False,
                )
                return ("ok", lote.id)
            except nfse_lote.LoteError as exc:
                return ("lote_error", str(exc))
            except IntegrityError as exc:
                return ("integrity_error", str(exc.orig))

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(lambda _: chamar_criar_lote(), range(2)))

    with sessions() as db:
        notas = db.query(NfseNota).filter_by(billing_id=billing_id).all()

    # O UNIQUE em nfse_notas.billing_id impede duplicata no banco, e
    # `criar_lote` agora trata o IntegrityError do perdedor da corrida
    # (SAVEPOINT por item + reaproveita o registro vencedor) — as duas
    # chamadas devem terminar "ok".
    assert len(notas) == 1, (
        f"Esperado exatamente 1 NfseNota (UNIQUE deveria impedir duplicata), achou {len(notas)}"
    )
    tipos = sorted(r[0] for r in resultados)
    assert tipos == ["ok", "ok"], (
        f"Corrida ainda quebra o check-then-insert de NfseNota em criar_lote: {resultados}"
    )
