"""SEC-02 — rotação de refresh token com consumo único.

- Duas rotações simultâneas do MESMO token (PostgreSQL real, barreira entre
  a leitura e a gravação): exatamente uma vence; a outra recebe 409 e não
  gera sucessor. Antes: as duas passavam e a sessão bifurcava.
- Reapresentar o token antigo dentro da janela de disputa: 409, sem revogar.
- Reapresentar depois da janela: reuso → família inteira revogada (401).
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app.api.v1.endpoints import auth as auth_module
from app.core.config import settings
from app.core.security import get_password_hash
from app.db.session import Base, get_db
from app.main import app
from app.models.enums import UserRole
from app.models.refresh_token import RefreshToken
from app.models.user import User

PREFIX = "/api/v1/auth"
SENHA = "Senha@123"


def _user(db, email="rotacao@test.local"):
    u = User(
        name="Rotação", email=email, password_hash=get_password_hash(SENHA),
        role=UserRole.ADMIN, active=True, is_deleted=False,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _login(tc, email):
    r = tc.post(PREFIX + "/login", json={"email": email, "password": SENHA})
    assert r.status_code == 200, r.text
    return r.cookies["refresh_token"]


class TestJanelaDeDisputa:
    def test_token_recem_rotacionado_devolve_409_sem_revogar(self, http_unauth, db):
        user = _user(db)
        antigo = _login(http_unauth, user.email)
        r1 = http_unauth.post(PREFIX + "/refresh")
        assert r1.status_code == 200
        novo = r1.cookies["refresh_token"]

        # segunda aba ainda com o cookie antigo, milissegundos depois
        http_unauth.cookies.set("refresh_token", antigo)
        r2 = http_unauth.post(PREFIX + "/refresh")
        assert r2.status_code == 409
        assert "refresh_token" not in r2.cookies  # nada foi emitido

        # a sessão vencedora segue válida: a aba repete com o cookie novo
        http_unauth.cookies.set("refresh_token", novo)
        assert http_unauth.post(PREFIX + "/refresh").status_code == 200
        assert db.scalar(select(func.count()).select_from(RefreshToken).where(RefreshToken.revoked_at.isnot(None))) == 0

    def test_reuso_apos_a_janela_revoga_a_familia(self, http_unauth, db, monkeypatch):
        monkeypatch.setattr(settings, "refresh_reuse_grace_seconds", 0)
        user = _user(db)
        antigo = _login(http_unauth, user.email)
        novo = http_unauth.post(PREFIX + "/refresh").cookies["refresh_token"]

        http_unauth.cookies.set("refresh_token", antigo)
        assert http_unauth.post(PREFIX + "/refresh").status_code == 401

        http_unauth.cookies.set("refresh_token", novo)
        assert http_unauth.post(PREFIX + "/refresh").status_code == 401  # família revogada

    def test_token_revogado_nunca_cai_na_janela(self, http_unauth, db):
        user = _user(db)
        _login(http_unauth, user.email)
        cookie = http_unauth.cookies.get("refresh_token")
        assert http_unauth.post(PREFIX + "/logout").status_code == 200
        http_unauth.cookies.set("refresh_token", cookie)
        assert http_unauth.post(PREFIX + "/refresh").status_code == 401

    def test_rotacao_grava_rotated_at_no_pai(self, http_unauth, db):
        user = _user(db)
        _login(http_unauth, user.email)
        http_unauth.post(PREFIX + "/refresh")
        pai = db.scalars(select(RefreshToken).order_by(RefreshToken.id)).first()
        db.refresh(pai)
        assert pai.replaced_by_jti is not None and pai.rotated_at is not None


# ---------------------------------------------------------------------------
# PostgreSQL real: duas rotações concorrentes
# ---------------------------------------------------------------------------

@pytest.fixture()
def pg_sessions():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL não configurada")
    schema = f"test_refresh_{uuid4().hex}"
    admin_engine = create_engine(url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, future=True, pool_size=10, connect_args={"options": f"-csearch_path={schema}"})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_db():
        s = sessions()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_db
    try:
        yield sessions
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.postgres
class TestRotacaoConcorrentePostgres:
    def test_duas_rotacoes_simultaneas_uma_vence(self, pg_sessions, monkeypatch):
        with pg_sessions() as s:
            user = _user(s, "concorrente@test.local")
            email = user.email

        login_tc = TestClient(app, raise_server_exceptions=False)
        cookie = _login(login_tc, email)

        # Barreira DEPOIS da leitura/validação e ANTES do UPDATE: é o
        # interleaving que a auditoria reproduziu (T1 e T2 leem "não usado").
        barreira = Barrier(2, timeout=15)
        original = auth_module.create_refresh_token

        def create_com_barreira(*args, **kwargs):
            resultado = original(*args, **kwargs)
            barreira.wait()
            return resultado

        monkeypatch.setattr(auth_module, "create_refresh_token", create_com_barreira)

        def rotacionar(_):
            tc = TestClient(app, raise_server_exceptions=False)
            tc.cookies.set("refresh_token", cookie)
            return tc.post(PREFIX + "/refresh")

        with ThreadPoolExecutor(max_workers=2) as pool:
            respostas = list(pool.map(rotacionar, range(2)))

        codigos = sorted(r.status_code for r in respostas)
        assert codigos == [200, 409], [r.text for r in respostas]

        with pg_sessions() as s:
            linhas = s.scalars(select(RefreshToken)).all()
            # pai + UM sucessor; nada revogado (disputa legítima)
            assert len(linhas) == 2
            assert all(l.revoked_at is None for l in linhas)
            filhos = [l for l in linhas if l.replaced_by_jti is None]
            assert len(filhos) == 1

        # O vencedor continua renovando normalmente
        vencedor = next(r for r in respostas if r.status_code == 200)
        monkeypatch.setattr(auth_module, "create_refresh_token", original)
        tc = TestClient(app, raise_server_exceptions=False)
        tc.cookies.set("refresh_token", vencedor.cookies["refresh_token"])
        assert tc.post(PREFIX + "/refresh").status_code == 200

    def test_replay_posterior_revoga_familia_no_postgres(self, pg_sessions, monkeypatch):
        monkeypatch.setattr(settings, "refresh_reuse_grace_seconds", 0)
        with pg_sessions() as s:
            email = _user(s, "replay@test.local").email
        tc = TestClient(app, raise_server_exceptions=False)
        antigo = _login(tc, email)
        novo = tc.post(PREFIX + "/refresh").cookies["refresh_token"]

        tc.cookies.set("refresh_token", antigo)
        assert tc.post(PREFIX + "/refresh").status_code == 401
        tc.cookies.set("refresh_token", novo)
        assert tc.post(PREFIX + "/refresh").status_code == 401
        with pg_sessions() as s:
            assert s.scalar(select(func.count()).select_from(RefreshToken).where(RefreshToken.revoked_at.is_(None))) == 0
