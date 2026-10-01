"""SEC-04 — política de senha única em criação, edição e reset.

Antes: UserCreate aceitava senha vazia e UserUpdate aceitava um caractere;
só o reset e o cadastro validavam. Login NÃO aplica a política (contas
antigas continuam entrando; a regra vale na próxima troca).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.core.password_policy import MAX_BYTES, password_problems, validate_password
from app.core.security import get_password_hash, verify_password
from app.models.enums import UserRole
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User

USERS = "/api/v1/users"
VALIDA = "Senha@123"


class TestPoliticaUnitaria:
    @pytest.mark.parametrize("senha", [
        None, "", "a", "        ", "Ab1@", "senha@123", "SENHA@123", "Senha@abc", "Senha123",
        "Senha@12\x00", "Senha@12\n", "\t\t\t\t\t\t\t\t",
    ])
    def test_rejeita(self, senha):
        assert password_problems(senha)
        with pytest.raises(ValueError):
            validate_password(senha)

    @pytest.mark.parametrize("senha", [
        VALIDA, "Ação#2026", "Pássaro 9x", "ÇÃO-ção-1", "Senha com espaço 1",
    ])
    def test_aceita(self, senha):
        assert password_problems(senha) == []
        assert validate_password(senha) == senha

    def test_limite_do_bcrypt_em_bytes(self):
        ascii_72 = "Aa1@" + "x" * (MAX_BYTES - 4)
        assert len(ascii_72.encode()) == 72 and password_problems(ascii_72) == []
        assert password_problems(ascii_72 + "y")  # 73 bytes
        # 4 ASCII + 35 'ç' = 74 bytes com só 39 caracteres: acento conta em dobro
        acentuada = "Aa1@" + "ç" * 35
        assert len(acentuada) < MAX_BYTES < len(acentuada.encode())
        assert password_problems(acentuada)

    def test_mensagem_nunca_contem_a_senha(self):
        senha = "segredo-sem-maiuscula-1"
        assert all(senha not in p for p in password_problems(senha))


def _seed(db, email="alvo-senha@test.local"):
    u = User(
        name="Alvo", email=email, password_hash=get_password_hash(VALIDA),
        role=UserRole.OPERATIONAL, active=True, is_deleted=False,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


class TestAdministracaoDeUsuarios:
    @pytest.mark.parametrize("senha", ["", "a", "12345678", "semmaiuscula1!"])
    def test_criacao_com_senha_fraca_422(self, http, db, senha):
        r = http.post(USERS + "/", json={
            "name": "Novo", "email": "novo@test.local", "role": "operacional", "password": senha,
        })
        assert r.status_code == 422
        assert db.query(User).filter(User.email == "novo@test.local").count() == 0

    def test_criacao_valida(self, http, db):
        r = http.post(USERS + "/", json={
            "name": "Novo", "email": "novo@test.local", "role": "operacional", "password": "Nova#Senha9",
        })
        assert r.status_code == 200, r.text
        user = db.query(User).filter(User.email == "novo@test.local").one()
        assert verify_password("Nova#Senha9", user.password_hash)

    @pytest.mark.parametrize("senha", ["", "a", "x" * 80])
    def test_edicao_com_senha_fraca_422_e_hash_intacto(self, http, db, senha):
        user = _seed(db)
        hash_antes = user.password_hash
        r = http.put(f"{USERS}/{user.id}", json={"password": senha})
        assert r.status_code == 422
        db.refresh(user)
        assert user.password_hash == hash_antes

    def test_edicao_sem_senha_mantem_a_atual(self, http, db):
        user = _seed(db)
        r = http.put(f"{USERS}/{user.id}", json={"name": "Renomeado", "password": None})
        assert r.status_code == 200
        db.refresh(user)
        assert verify_password(VALIDA, user.password_hash)
        assert user.tokens_valid_from is None  # não houve troca de senha

    def test_senha_rejeitada_nao_aparece_na_resposta_nem_no_log(self, http, db, caplog):
        user = _seed(db)
        senha = "vazamento-teste-sem-maiuscula"
        with caplog.at_level(logging.DEBUG):
            r = http.put(f"{USERS}/{user.id}", json={"password": senha})
        assert r.status_code == 422
        assert senha not in r.text
        assert senha not in caplog.text


class TestReset:
    def test_reset_com_senha_fraca_422_e_token_preservado(self, http_unauth, db):
        import hashlib
        user = _seed(db)
        db.add(PasswordResetToken(
            user_id=user.id, token_hash=hashlib.sha256(b"tok-fraco").hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        ))
        db.commit()
        r = http_unauth.post("/api/v1/auth/reset-password", json={
            "token": "tok-fraco", "new_password": "a", "password_confirmation": "a",
        })
        assert r.status_code == 422
        assert db.query(PasswordResetToken).one().used_at is None

    def test_senhas_divergentes_nao_voltam_no_422(self, http_unauth):
        r = http_unauth.post("/api/v1/auth/reset-password", json={
            "token": "qualquer", "new_password": "Primeira#123", "password_confirmation": "Segunda#456",
        })
        assert r.status_code == 422
        assert "Primeira#123" not in r.text and "Segunda#456" not in r.text
        assert "qualquer" not in r.text
        assert "não conferem" in r.text  # a mensagem útil continua

    def test_login_com_senha_antiga_fraca_continua_funcionando(self, http_unauth, db):
        """A política não tranca quem já tem senha fraca: vale na próxima troca."""
        u = User(
            name="Legado", email="legado@test.local", password_hash=get_password_hash("123"),
            role=UserRole.OPERATIONAL, active=True, is_deleted=False,
        )
        db.add(u)
        db.commit()
        r = http_unauth.post("/api/v1/auth/login", json={"email": "legado@test.local", "password": "123"})
        assert r.status_code == 200
