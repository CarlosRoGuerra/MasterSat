"""SEC-03 — bootstrap único e recuperação controlada.

Antes: _seed_admin reativava a conta inicial excluída a cada restart,
redefinia a senha, escrevia a senha no log e não cortava tokens antigos.
"""
from __future__ import annotations

import logging
import runpy
import sys

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import get_password_hash, verify_password
from app.main import _seed_admin, app
from app.models.audit_log import AuditLog
from app.models.enums import UserRole
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.admin_bootstrap import (
    CRIADO, JA_PROVISIONADO, SEM_SENHA, SENHA_FORA_DA_POLITICA,
    RecuperacaoRecusada, recuperar_admin, seed_initial_admin,
)

INICIAL = "Inicial#2026x"
EMAIL = "admin@rastreamento.local"


@pytest.fixture()
def bootstrap_env(monkeypatch):
    monkeypatch.setattr(settings, "initial_admin_email", EMAIL)
    monkeypatch.setattr(settings, "initial_admin_password", INICIAL)


def _admin(db):
    return db.query(User).filter(User.email == EMAIL).first()


class TestBootstrap:
    def test_instalacao_nova_cria_admin_sem_senha_no_log(self, db, bootstrap_env, caplog):
        with caplog.at_level(logging.DEBUG):
            assert seed_initial_admin(db) == CRIADO
        admin = _admin(db)
        assert admin.role == UserRole.ADMIN and admin.active and not admin.is_deleted
        assert verify_password(INICIAL, admin.password_hash)
        assert INICIAL not in caplog.text

    def test_sem_senha_configurada_nao_cria_nem_gera(self, db, bootstrap_env, monkeypatch, caplog):
        monkeypatch.setattr(settings, "initial_admin_password", "")
        with caplog.at_level(logging.DEBUG):
            assert seed_initial_admin(db) == SEM_SENHA
        assert db.query(User).count() == 0
        assert "reset_admin_senha.py --criar" in caplog.text
        assert "senha gerada" not in caplog.text.lower()

    def test_senha_fora_da_politica_nao_cria_e_nao_loga_a_senha(self, db, bootstrap_env, monkeypatch, caplog):
        monkeypatch.setattr(settings, "initial_admin_password", "admin123")
        with caplog.at_level(logging.DEBUG):
            assert seed_initial_admin(db) == SENHA_FORA_DA_POLITICA
        assert db.query(User).count() == 0
        assert "admin123" not in caplog.text

    def test_existindo_qualquer_usuario_nao_mexe_em_nada(self, db, bootstrap_env):
        db.add(User(name="Op", email="op@test.local", role=UserRole.OPERATIONAL,
                    password_hash=get_password_hash("x"), active=True, is_deleted=False))
        db.commit()
        assert seed_initial_admin(db) == JA_PROVISIONADO
        assert _admin(db) is None


class TestRestartComAdminExcluido:
    def test_exclusao_sobrevive_ao_restart_e_tokens_antigos_nao_voltam(self, db, http_unauth, bootstrap_env, caplog):
        _seed_admin()  # primeiro boot
        admin = _admin(db)
        tc = TestClient(app, raise_server_exceptions=False)
        login = tc.post("/api/v1/auth/login", json={"email": EMAIL, "password": INICIAL})
        assert login.status_code == 200
        access = login.json()["access_token"]
        cookie = login.cookies["refresh_token"]

        # Admin inicial excluído (soft delete) por outro administrador
        admin.is_deleted = True
        db.commit()

        with caplog.at_level(logging.DEBUG):
            _seed_admin()  # restart
            _seed_admin()  # outro worker / outro restart
        db.expire_all()
        admin = _admin(db)
        assert admin.is_deleted is True
        assert db.query(User).count() == 1
        assert INICIAL not in caplog.text

        assert tc.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"}).status_code == 401
        tc.cookies.set("refresh_token", cookie)
        assert tc.post("/api/v1/auth/refresh").status_code == 401
        assert tc.post("/api/v1/auth/login", json={"email": EMAIL, "password": INICIAL}).status_code == 401


class TestRecuperacao:
    def _admin_excluido(self, db):
        u = User(name="Adm", email=EMAIL, role=UserRole.ADMIN, active=True, is_deleted=True,
                 password_hash=get_password_hash("Antiga#123"))
        db.add(u)
        db.commit()
        db.add(RefreshToken(user_id=u.id, jti="j1", family="f1",
                            expires_at=__import__("datetime").datetime(2099, 1, 1)))
        db.commit()
        return u

    def test_excluido_sem_reativar_e_recusado(self, db):
        u = self._admin_excluido(db)
        with pytest.raises(RecuperacaoRecusada, match="--reativar"):
            recuperar_admin(db, EMAIL, "Nova#Senha2026")
        db.refresh(u)
        assert u.is_deleted and verify_password("Antiga#123", u.password_hash)

    def test_reativar_explicito_encerra_sessoes_e_audita_sem_senha(self, db):
        u = self._admin_excluido(db)
        r = recuperar_admin(db, EMAIL, "Nova#Senha2026", reativar=True, operador="suporte-ti")
        db.refresh(u)
        assert r.reativado and not u.is_deleted and u.active
        assert verify_password("Nova#Senha2026", u.password_hash)
        assert u.tokens_valid_from is not None
        assert db.query(RefreshToken).filter(RefreshToken.revoked_at.is_(None)).count() == 0
        trilha = db.query(AuditLog).one()
        assert trilha.entity_id == u.id and "suporte-ti" in trilha.user_name
        assert "Nova#Senha2026" not in (trilha.description or "")

    def test_senha_fraca_recusada(self, db):
        self._admin_excluido(db)
        with pytest.raises(RecuperacaoRecusada, match="política"):
            recuperar_admin(db, EMAIL, "123", reativar=True)

    def test_nao_admin_recusado(self, db):
        db.add(User(name="Op", email="op@test.local", role=UserRole.OPERATIONAL,
                    password_hash="x", active=True, is_deleted=False))
        db.commit()
        with pytest.raises(RecuperacaoRecusada, match="não é administrador"):
            recuperar_admin(db, "op@test.local", "Nova#Senha2026")

    def test_criar_so_em_banco_vazio(self, db):
        r = recuperar_admin(db, "novo-admin@test.local", "Nova#Senha2026", criar=True)
        assert r.criado
        with pytest.raises(RecuperacaoRecusada, match="já tem usuários"):
            recuperar_admin(db, "outro@test.local", "Nova#Senha2026", criar=True)

    def test_script_gerar_mostra_senha_so_no_terminal(self, db, capsys, caplog, monkeypatch):
        # o script importa app.db.session.SessionLocal, que a fixture db já
        # aponta para o banco do teste
        self._admin_excluido(db)
        monkeypatch.setattr(sys, "argv", ["reset_admin_senha.py", "--email", EMAIL, "--gerar", "--reativar"])
        with caplog.at_level(logging.DEBUG):
            runpy.run_path("scripts/reset_admin_senha.py", run_name="__main__")
        saida = capsys.readouterr().out
        senha = saida.split("Senha gerada (mostrada só agora): ")[1].strip()
        db.expire_all()
        assert verify_password(senha, _admin(db).password_hash)
        assert senha not in caplog.text
