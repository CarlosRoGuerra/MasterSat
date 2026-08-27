"""
Testes de integração para /api/v1/auth.

Cobertos:
- POST /login       → credenciais corretas, senha errada, email não existe,
                      usuário inativo, role CLIENT bloqueado
- POST /refresh     → cookie válido, ausente, inválido, reuso de token rotacionado
- POST /logout      → revoga o refresh token no servidor (não só limpa cookie)
- POST /forgot-password → email existente, email inexistente (resposta genérica)
- POST /reset-password  → token válido, token expirado, token já usado, derruba refresh tokens
- GET  /me          → retorna usuário autenticado
- POST /register-client → sempre retorna 403 (cadastro desativado)

Refresh token vive em cookie httpOnly (não no corpo JSON) — os testes usam o
cookie jar do TestClient (como um navegador) em vez de ler `refresh_token` do
JSON de resposta.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.security import get_password_hash
from app.models.enums import UserRole
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User

PREFIX = "/api/v1/auth"


def _create_user(db, role=UserRole.ADMIN, active=True, email="admin@test.local", password="Senha@123"):
    u = User(
        name="Admin Teste",
        email=email,
        password_hash=get_password_hash(password),
        role=role,
        active=active,
        is_deleted=False,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------

class TestLogin:
    def test_success_admin(self, http_unauth, db):
        user = _create_user(db)
        r = http_unauth.post(PREFIX + "/login", json={"email": user.email, "password": "Senha@123"})
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data
        assert "refresh_token" not in data, "refresh token não pode voltar no corpo JSON (só cookie httpOnly)"
        assert data["token_type"] == "bearer"
        assert "refresh_token" in r.cookies

    def test_wrong_password(self, http_unauth, db):
        _create_user(db)
        r = http_unauth.post(PREFIX + "/login", json={"email": "admin@test.local", "password": "ERRADA"})
        assert r.status_code == 401

    def test_nonexistent_email(self, http_unauth, db):
        r = http_unauth.post(PREFIX + "/login", json={"email": "nao@existe.com", "password": "qualquer"})
        assert r.status_code == 401

    def test_inactive_user_denied(self, http_unauth, db):
        _create_user(db, active=False, email="inativo@test.local")
        r = http_unauth.post(PREFIX + "/login", json={"email": "inativo@test.local", "password": "Senha@123"})
        assert r.status_code == 401

    def test_client_role_denied(self, http_unauth, db):
        _create_user(db, role=UserRole.CLIENT, email="cliente@test.local")
        r = http_unauth.post(PREFIX + "/login", json={"email": "cliente@test.local", "password": "Senha@123"})
        assert r.status_code == 403

    def test_email_case_insensitive(self, http_unauth, db):
        _create_user(db, email="admin@test.local")
        r = http_unauth.post(PREFIX + "/login", json={"email": "ADMIN@TEST.LOCAL", "password": "Senha@123"})
        assert r.status_code == 200

    def test_email_whitespace_trimmed(self, http_unauth, db):
        _create_user(db, email="admin@test.local")
        r = http_unauth.post(PREFIX + "/login", json={"email": "  admin@test.local  ", "password": "Senha@123"})
        assert r.status_code == 200

    def test_deleted_user_denied(self, http_unauth, db):
        u = _create_user(db, email="deletado@test.local")
        u.is_deleted = True
        db.commit()
        r = http_unauth.post(PREFIX + "/login", json={"email": "deletado@test.local", "password": "Senha@123"})
        assert r.status_code == 401

    def test_missing_fields_returns_422(self, http_unauth, db):
        r = http_unauth.post(PREFIX + "/login", json={"email": "x@x.com"})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /refresh
# ---------------------------------------------------------------------------

class TestRefresh:
    def test_valid_refresh_returns_new_tokens(self, http_unauth, db):
        user = _create_user(db)
        login_r = http_unauth.post(PREFIX + "/login", json={"email": user.email, "password": "Senha@123"})
        assert "refresh_token" in login_r.cookies
        r = http_unauth.post(PREFIX + "/refresh")
        assert r.status_code == 200
        assert "access_token" in r.json()
        # rotação: o cookie devolvido não pode ser o mesmo apresentado
        assert r.cookies["refresh_token"] != login_r.cookies["refresh_token"]

    def test_missing_cookie_returns_401(self, http_unauth, db):
        r = http_unauth.post(PREFIX + "/refresh")
        assert r.status_code == 401

    def test_invalid_token_returns_401(self, http_unauth, db):
        http_unauth.cookies.set("refresh_token", "token.invalido.xyz")
        r = http_unauth.post(PREFIX + "/refresh")
        assert r.status_code == 401

    def test_access_token_rejected_as_refresh(self, http_unauth, db):
        user = _create_user(db)
        login_r = http_unauth.post(PREFIX + "/login", json={"email": user.email, "password": "Senha@123"})
        access_token = login_r.json()["access_token"]
        http_unauth.cookies.set("refresh_token", access_token)
        r = http_unauth.post(PREFIX + "/refresh")
        assert r.status_code == 401

    def test_reused_rotated_token_is_rejected_and_revokes_family(self, http_unauth, db):
        """Reuso de um refresh token já rotacionado é o sinal de token roubado:
        a família inteira (incluindo o token novo, legítimo) é revogada."""
        user = _create_user(db)
        login_r = http_unauth.post(PREFIX + "/login", json={"email": user.email, "password": "Senha@123"})
        old_token = login_r.cookies["refresh_token"]

        r1 = http_unauth.post(PREFIX + "/refresh")
        assert r1.status_code == 200
        new_token = r1.cookies["refresh_token"]

        # reapresenta o token antigo (já rotacionado) — reuso
        http_unauth.cookies.set("refresh_token", old_token)
        r2 = http_unauth.post(PREFIX + "/refresh")
        assert r2.status_code == 401

        # o token novo (que era válido) também foi revogado junto
        http_unauth.cookies.set("refresh_token", new_token)
        r3 = http_unauth.post(PREFIX + "/refresh")
        assert r3.status_code == 401


class TestLogout:
    def test_logout_revokes_refresh_token_server_side(self, http_unauth, db):
        user = _create_user(db)
        login_r = http_unauth.post(PREFIX + "/login", json={"email": user.email, "password": "Senha@123"})
        old_token = login_r.cookies["refresh_token"]

        r = http_unauth.post(PREFIX + "/logout")
        assert r.status_code == 200

        # mesmo reapresentando manualmente o cookie de antes do logout (ex.:
        # um cookie vazado), o servidor já revogou — não é só limpeza local.
        http_unauth.cookies.set("refresh_token", old_token)
        r2 = http_unauth.post(PREFIX + "/refresh")
        assert r2.status_code == 401

    def test_logout_without_cookie_still_succeeds(self, http_unauth):
        r = http_unauth.post(PREFIX + "/logout")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# POST /forgot-password
# ---------------------------------------------------------------------------

class TestForgotPassword:
    def test_existing_email_returns_200(self, http_unauth, db):
        user = _create_user(db)
        r = http_unauth.post(PREFIX + "/forgot-password", json={"email": user.email})
        assert r.status_code == 200
        assert "message" in r.json()

    def test_nonexistent_email_also_returns_200(self, http_unauth, db):
        r = http_unauth.post(PREFIX + "/forgot-password", json={"email": "nao@existe.com"})
        assert r.status_code == 200

    def test_debug_mode_off_hides_token(self, http_unauth, db):
        user = _create_user(db)
        r = http_unauth.post(PREFIX + "/forgot-password", json={"email": user.email})
        assert r.status_code == 200
        assert r.json().get("reset_token") is None

    def test_mensagem_identica_evita_enumeracao(self, http_unauth, db):
        # A resposta não pode revelar se o e-mail existe (enumeração de usuários).
        user = _create_user(db)
        existe = http_unauth.post(PREFIX + "/forgot-password", json={"email": user.email})
        nao_existe = http_unauth.post(PREFIX + "/forgot-password", json={"email": "nao@existe.com"})
        assert existe.json()["message"] == nao_existe.json()["message"]


# ---------------------------------------------------------------------------
# POST /reset-password
# ---------------------------------------------------------------------------

class TestResetPassword:
    def _make_token(self, db, user, expired=False):
        from uuid import uuid4
        token_str = uuid4().hex
        expires = datetime.now(timezone.utc) + (timedelta(hours=-1) if expired else timedelta(hours=1))
        t = PasswordResetToken(user_id=user.id, token=token_str, expires_at=expires)
        db.add(t)
        db.commit()
        return token_str

    def test_valid_token_resets_password(self, http_unauth, db):
        user = _create_user(db)
        token_str = self._make_token(db, user)
        r = http_unauth.post(PREFIX + "/reset-password", json={
            "token": token_str,
            "new_password": "NovaSenha@456",
            "password_confirmation": "NovaSenha@456",
        })
        assert r.status_code == 200

    def test_expired_token_returns_400(self, http_unauth, db):
        user = _create_user(db)
        token_str = self._make_token(db, user, expired=True)
        r = http_unauth.post(PREFIX + "/reset-password", json={
            "token": token_str,
            "new_password": "NovaSenha@456",
            "password_confirmation": "NovaSenha@456",
        })
        assert r.status_code == 400

    def test_invalid_token_returns_400(self, http_unauth, db):
        r = http_unauth.post(PREFIX + "/reset-password", json={
            "token": "tokeninvalido123",
            "new_password": "NovaSenha@456",
            "password_confirmation": "NovaSenha@456",
        })
        assert r.status_code == 400

    def test_token_cannot_be_reused(self, http_unauth, db):
        user = _create_user(db)
        token_str = self._make_token(db, user)
        payload = {"token": token_str, "new_password": "NovaSenha@456", "password_confirmation": "NovaSenha@456"}
        r1 = http_unauth.post(PREFIX + "/reset-password", json=payload)
        assert r1.status_code == 200
        r2 = http_unauth.post(PREFIX + "/reset-password", json=payload)
        assert r2.status_code == 400

    def test_reset_revoga_refresh_tokens_ativos(self, http_unauth, db):
        """Um refresh token roubado não pode sobreviver à troca de senha."""
        user = _create_user(db, email="alvo-reset@test.local")
        login_r = http_unauth.post(PREFIX + "/login", json={"email": user.email, "password": "Senha@123"})
        old_refresh_token = login_r.cookies["refresh_token"]

        token_str = self._make_token(db, user)
        r = http_unauth.post(PREFIX + "/reset-password", json={
            "token": token_str,
            "new_password": "NovaSenha@456",
            "password_confirmation": "NovaSenha@456",
        })
        assert r.status_code == 200

        http_unauth.cookies.set("refresh_token", old_refresh_token)
        r2 = http_unauth.post(PREFIX + "/refresh")
        assert r2.status_code == 401


# ---------------------------------------------------------------------------
# GET /me
# ---------------------------------------------------------------------------

class TestMe:
    def test_returns_authenticated_user(self, http):
        r = http.get(PREFIX + "/me")
        assert r.status_code == 200
        assert "id" in r.json()
        assert "email" in r.json()

    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.get(PREFIX + "/me")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /register-client (desativado)
# ---------------------------------------------------------------------------

class TestRegisterClient:
    def test_always_returns_403(self, http_unauth):
        r = http_unauth.post(PREFIX + "/register-client", json={
            "name": "Test", "cpf_cnpj": "12345678901", "type": "pf",
            "email": "test@test.com", "password": "Senha@123",
            "password_confirmation": "Senha@123",
            "zip_code": "01310100", "address_line": "Av Paulista",
            "address_number": "1", "neighborhood": "Bela Vista",
            "city": "São Paulo", "state": "SP",
        })
        assert r.status_code == 403
