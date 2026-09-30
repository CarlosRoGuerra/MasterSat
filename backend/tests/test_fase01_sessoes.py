"""SEC-06 — logout encerra a sessão inteira, access incluído.

Política escolhida: logout POR SESSÃO. O access token carrega 'sid' (a
família do refresh do mesmo login); logout revoga a família e o access dela
leva 401 na hora. Outro dispositivo do mesmo usuário segue logado. Para
derrubar todos: troca de senha (admin ou reset).

Antes: logout=200 e /auth/me com o access anterior=200 até expirar.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.models.enums import UserRole
from app.models.user import User

PREFIX = "/api/v1/auth"
SENHA = "Senha@123"


def _user(db, email="sessao@test.local", role=UserRole.ADMIN):
    u = User(
        name="Sessão", email=email, password_hash=get_password_hash(SENHA),
        role=role, active=True, is_deleted=False,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _dispositivo(email) -> tuple[TestClient, str]:
    tc = TestClient(app, raise_server_exceptions=False)
    r = tc.post(PREFIX + "/login", json={"email": email, "password": SENHA})
    assert r.status_code == 200, r.text
    return tc, r.json()["access_token"]


def _me(tc, access):
    return tc.get(PREFIX + "/me", headers={"Authorization": f"Bearer {access}"}).status_code


class TestLogoutPorSessao:
    def test_logout_invalida_o_access_da_sessao(self, http_unauth, db):
        user = _user(db)
        tc, access = _dispositivo(user.email)
        assert _me(tc, access) == 200

        assert tc.post(PREFIX + "/logout").status_code == 200

        # access copiado antes do logout não vale mais
        outro = TestClient(app, raise_server_exceptions=False)
        assert _me(outro, access) == 401

    def test_outro_dispositivo_continua_logado(self, http_unauth, db):
        user = _user(db)
        notebook, access_nb = _dispositivo(user.email)
        celular, access_cel = _dispositivo(user.email)

        assert notebook.post(PREFIX + "/logout").status_code == 200

        assert _me(notebook, access_nb) == 401
        assert _me(celular, access_cel) == 200
        r = celular.post(PREFIX + "/refresh")
        assert r.status_code == 200
        assert _me(celular, r.json()["access_token"]) == 200

    def test_logout_so_com_bearer_tambem_encerra(self, http_unauth, db):
        user = _user(db)
        tc, access = _dispositivo(user.email)
        cookie = tc.cookies.get("refresh_token")
        tc.cookies.clear()

        r = tc.post(PREFIX + "/logout", headers={"Authorization": f"Bearer {access}"})
        assert r.status_code == 200
        assert _me(tc, access) == 401
        tc.cookies.set("refresh_token", cookie)
        assert tc.post(PREFIX + "/refresh").status_code == 401

    def test_logout_com_lixo_nao_quebra(self, http_unauth):
        r = http_unauth.post(PREFIX + "/logout", headers={"Authorization": "Bearer nao.e.jwt"})
        assert r.status_code == 200

    def test_refresh_devolve_access_da_mesma_sessao(self, http_unauth, db):
        user = _user(db)
        tc, _ = _dispositivo(user.email)
        novo_access = tc.post(PREFIX + "/refresh").json()["access_token"]
        assert _me(tc, novo_access) == 200
        tc.post(PREFIX + "/logout")
        assert _me(tc, novo_access) == 401


class TestTransicaoDeTokensLegados:
    def test_access_sem_sid_e_recusado_e_o_refresh_recupera(self, http_unauth, db):
        user = _user(db)
        tc, _ = _dispositivo(user.email)
        legado = create_access_token(str(user.id), name=user.name, role="admin")  # sem sid
        assert _me(tc, legado) == 401

        # O frontend reage ao 401 renovando pelo cookie — sem novo login
        r = tc.post(PREFIX + "/refresh")
        assert r.status_code == 200
        assert _me(tc, r.json()["access_token"]) == 200

    def test_sid_de_outro_usuario_nao_serve(self, http_unauth, db):
        alvo = _user(db, "alvo@test.local")
        atacante = _user(db, "atacante@test.local")
        _, access_alvo = _dispositivo(alvo.email)
        from jose import jwt
        sid_alvo = jwt.decode(access_alvo, settings.secret_key, algorithms=[settings.algorithm])["sid"]
        forjado = create_access_token(str(atacante.id), session_id=sid_alvo)
        assert _me(TestClient(app, raise_server_exceptions=False), forjado) == 401


class TestTrocaDeSenhaDerrubaTodasAsSessoes:
    def test_admin_troca_senha_do_usuario(self, http_unauth, db):
        admin = _user(db, "admin-sessoes@test.local")
        operador = _user(db, "operador-sessoes@test.local", role=UserRole.OPERATIONAL)
        tc_op, access_op = _dispositivo(operador.email)
        tc_adm, access_adm = _dispositivo(admin.email)

        r = tc_adm.put(
            f"/api/v1/users/{operador.id}",
            json={"password": "NovaSenha#2026"},
            headers={"Authorization": f"Bearer {access_adm}"},
        )
        assert r.status_code == 200, r.text

        assert _me(tc_op, access_op) == 401
        assert tc_op.post(PREFIX + "/refresh").status_code == 401
        # a sessão do admin que fez a troca não é afetada
        assert _me(tc_adm, access_adm) == 200
