"""
Shared fixtures for all test modules.

Strategy:
- Each test function gets a brand-new SQLite :memory: database so commits
  inside endpoint handlers never bleed between tests.
- FastAPI dependency overrides inject the test session and a fake user so no
  real PostgreSQL or JWT logic is needed.
- slowapi rate limiter is disabled globally so login/auth tests don't hit limits.
"""
from __future__ import annotations

import os

# Must be set BEFORE importing anything from app so that pydantic_settings
# picks up the SQLite URL instead of the default PostgreSQL URL, and so that
# SQLAlchemy does not try to import psycopg at module level.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("MULTIPORTAL_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-tests-only")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from datetime import date  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

# Importing app registers all SQLAlchemy models with Base's metadata.
from app.main import app  # noqa: F401, E402 (side-effect import)
from app.api.deps import get_current_user  # noqa: E402
from app.core.limiter import limiter  # noqa: E402
import app.db.session as db_session_module  # noqa: E402
from app.db.session import Base, get_db  # noqa: E402
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus, ClientStatus, OrderStatus, OrderType, TrackerStatus, UserRole
from app.models.plan import Plan
from app.models.service_order import ServiceOrder
from app.models.service_product import ServiceProduct
from app.models.tracker import Tracker
from app.models.uninstall_event import UninstallEvent
from app.models.user import User
from app.models.vehicle import Vehicle


# ---------------------------------------------------------------------------
# Disable slowapi rate limiting globally during tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    # O atributo real que o slowapi checa é "enabled" (sem underscore) — ver
    # extension.py: "if not _endpoint_key or not self.enabled". Com o nome
    # errado, isto era um no-op silencioso: os testes de auth continuavam
    # sujeitos ao limite de 5/minute do login, só não estourava porque o
    # storage era em memória de processo e cada suíte de teste tinha poucos
    # POSTs em /login. Com storage_uri=redis (SEC-03/INT-01), o contador
    # passou a persistir entre execuções do pytest e o bug ficou visível.
    limiter.enabled = False
    yield
    limiter.enabled = True


@pytest.fixture(autouse=True)
def _clear_ailos_fernet_cache():
    """Avoid leaking a cached Fernet instance (built from one test's key) into another."""
    from app.core.crypto import _fernet
    _fernet.cache_clear()
    yield
    _fernet.cache_clear()


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _make_engine():
    # StaticPool ensures all sessions share the same in-memory connection,
    # so tables created by create_all() are visible to every session.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db(monkeypatch):
    """Fresh in-memory SQLite session per test."""
    engine = _make_engine()
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    # Código que abre sua PRÓPRIA sessão via app.db.session.SessionLocal (ex.:
    # a sessão dedicada de log em ailos_client._log_call, ou os jobs de
    # background em main._run_locked) precisa cair no MESMO banco :memory:
    # isolado deste teste — senão escreveria silenciosamente num banco
    # desconectado (o engine "real", montado a partir de DATABASE_URL).
    monkeypatch.setattr(db_session_module, 'SessionLocal', Session)
    session = Session()
    yield session
    session.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# User factories
# ---------------------------------------------------------------------------

def _fake_user(role: UserRole, uid: int = 1) -> User:
    return User(
        id=uid,
        name=f"Test {role.value.capitalize()}",
        email=f"{role.value}@test.local",
        role=role,
        active=True,
        is_deleted=False,
        password_hash="not_a_real_hash",
    )


# ---------------------------------------------------------------------------
# HTTP client factories
# ---------------------------------------------------------------------------

def _test_client(db_session, user: User | None) -> TestClient:
    """Build a TestClient with overridden DB and (optional) auth."""
    app.dependency_overrides[get_db] = lambda: db_session
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    elif get_current_user in app.dependency_overrides:
        del app.dependency_overrides[get_current_user]
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def http(db):
    """Authenticated as ADMIN."""
    tc = _test_client(db, _fake_user(UserRole.ADMIN, uid=1))
    yield tc
    app.dependency_overrides.clear()


@pytest.fixture()
def http_op(db):
    """Authenticated as OPERATIONAL."""
    tc = _test_client(db, _fake_user(UserRole.OPERATIONAL, uid=2))
    yield tc
    app.dependency_overrides.clear()


@pytest.fixture()
def http_fin(db):
    """Authenticated as FINANCIAL."""
    tc = _test_client(db, _fake_user(UserRole.FINANCIAL, uid=3))
    yield tc
    app.dependency_overrides.clear()


@pytest.fixture()
def http_cliente(db):
    """Authenticated as CLIENT (lowest privilege, should be denied on admin ops)."""
    tc = _test_client(db, _fake_user(UserRole.CLIENT, uid=4))
    yield tc
    app.dependency_overrides.clear()


@pytest.fixture()
def http_unauth(db):
    """No authentication override — requests without a token return 401."""
    app.dependency_overrides[get_db] = lambda: db
    if get_current_user in app.dependency_overrides:
        del app.dependency_overrides[get_current_user]
    tc = TestClient(app, raise_server_exceptions=False)
    yield tc
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Database entity fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def plan(db) -> Plan:
    p = Plan(name="Plano Teste", price=Decimal("99.90"), active=True, billing_interval_months=1)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def plan2(db) -> Plan:
    p = Plan(name="Plano Premium", price=Decimal("199.90"), active=True, billing_interval_months=1)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def cliente(db) -> Client:
    c = Client(
        name="João Silva",
        cpf_cnpj="12345678901",
        type="pf",
        status=ClientStatus.ACTIVE,
        email="joao@test.local",
        phone="11999990000",
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture()
def outro_cliente(db) -> Client:
    c = Client(
        name="Maria Santos",
        cpf_cnpj="98765432100",
        type="pf",
        status=ClientStatus.ACTIVE,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture()
def veiculo(db, cliente) -> Vehicle:
    v = Vehicle(
        client_id=cliente.id,
        plate="ABC1D23",
        type="passeio",
        chassis="9BWZZZ377VT004251",
        brand="Toyota",
        model="Corolla",
        year=2022,
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


@pytest.fixture()
def veiculo_outro_cliente(db, outro_cliente) -> Vehicle:
    v = Vehicle(
        client_id=outro_cliente.id,
        plate="XYZ9Z99",
        type="passeio",
        chassis="9BWZZZ377VT009999",
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


@pytest.fixture()
def rastreador(db) -> Tracker:
    t = Tracker(
        imei="123456789012345",
        brand="Teltonika",
        model="FMB920",
        status=TrackerStatus.STOCK,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@pytest.fixture()
def rastreador_instalado(db, cliente, veiculo) -> Tracker:
    t = Tracker(
        imei="987654321098765",
        brand="Teltonika",
        model="FMB140",
        status=TrackerStatus.INSTALLED,
        client_id=cliente.id,
        vehicle_id=veiculo.id,
        install_date=date(2024, 1, 15),
        external_manufacturer_id=10,
        serial_number="987654321098765",
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@pytest.fixture()
def contrato(db, cliente, plan, veiculo, rastreador_instalado) -> Contract:
    c = Contract(
        client_id=cliente.id,
        plan_id=plan.id,
        vehicle_id=veiculo.id,
        tracker_id=rastreador_instalado.id,
        start_date=date(2024, 1, 15),
        status="ativo",
        billing_day=15,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture()
def billing_pendente(db, contrato) -> Billing:
    b = Billing(
        contract_id=contrato.id,
        client_id=contrato.client_id,
        amount=Decimal("99.90"),
        due_date=date(2099, 12, 31),
        status=BillingStatus.PENDING,
        billing_type="recorrente",
        period_label="12/2099",
        title="Plano Teste",
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


@pytest.fixture()
def billing_vencida(db, contrato) -> Billing:
    b = Billing(
        contract_id=contrato.id,
        client_id=contrato.client_id,
        amount=Decimal("99.90"),
        due_date=date(2020, 1, 1),
        status=BillingStatus.OVERDUE,
        billing_type="recorrente",
        period_label="01/2020",
        title="Plano Teste Vencida",
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


@pytest.fixture()
def produto_servico(db) -> ServiceProduct:
    p = ServiceProduct(
        name="Taxa de Instalação",
        category="taxa",
        default_price=Decimal("150.00"),
        description="Taxa padrão de instalação",
        active=True,
        allow_installments=False,
        remove_after_payment=True,
        auto_add_on_uninstall=False,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def produto_desinstalacao(db) -> ServiceProduct:
    p = ServiceProduct(
        name="Taxa de Desinstalação",
        category="taxa",
        default_price=Decimal("120.00"),
        description="Taxa padrão de desinstalação",
        active=True,
        allow_installments=False,
        remove_after_payment=True,
        auto_add_on_uninstall=True,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def ordem_servico(db, cliente, veiculo) -> ServiceOrder:
    o = ServiceOrder(
        number="OS-2025-001",
        type=OrderType.INSTALL,
        status=OrderStatus.OPEN,
        client_id=cliente.id,
        vehicle_id=veiculo.id,
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


@pytest.fixture()
def uninstall_event(db, cliente, veiculo, rastreador_instalado, contrato) -> UninstallEvent:
    e = UninstallEvent(
        vehicle_id=veiculo.id,
        tracker_id=rastreador_instalado.id,
        contract_id=contrato.id,
        client_id=cliente.id,
        uninstall_date=date(2025, 5, 10),
        fee_amount=Decimal("120.00"),
        status="pending",
        notes="Desinstalação teste",
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e
