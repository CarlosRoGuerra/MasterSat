"""SEC-05 — recuperação de senha que chega ao usuário, sem enumeração.

SMTP sempre mockado (nenhum e-mail real). Antes: HTTP 200, token gravado em
texto puro e nenhum e-mail enviado.
"""
from __future__ import annotations

import logging
import os
import re
import smtplib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.limiter import limiter
from app.core.security import get_password_hash, verify_password
from app.db.session import Base, get_db
from app.main import app
from app.models.enums import UserRole
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.services import password_reset as reset_service
from app.services.email_smtp import EmailConfigError
from app.services.password_reset import hash_reset_token

AUTH = "/api/v1/auth"
EMAIL = "esqueci@test.local"
NOVA = "NovaSenha#2026"


def _user(db, email=EMAIL, active=True, role=UserRole.OPERATIONAL):
    u = User(name="Esquecido", email=email, password_hash=get_password_hash("Antiga#123"),
             role=role, active=active, is_deleted=False)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def smtp():
    with patch("app.services.email_smtp.enviar_email") as enviar, \
            patch.object(reset_service.time, "sleep") as dormir:
        enviar.dormir = dormir
        yield enviar


def _token_do_email(chamada) -> str:
    corpo = chamada.args[3]
    m = re.search(r"/resetar-senha\?token=([A-Za-z0-9_\-%]+)", corpo)
    assert m, corpo
    from urllib.parse import unquote
    return unquote(m.group(1))


def _pedir(http_unauth, email=EMAIL):
    return http_unauth.post(AUTH + "/forgot-password", json={"email": email})


class TestEntrega:
    def test_email_existente_recebe_um_link_valido(self, http_unauth, db, smtp):
        user = _user(db)
        r = _pedir(http_unauth)
        assert r.status_code == 200

        assert smtp.call_count == 1
        chamada = smtp.call_args
        assert chamada.args[1] == EMAIL
        token = _token_do_email(chamada)
        assert settings.frontend_url.rstrip("/") + "/resetar-senha?token=" in chamada.args[3]
        assert "html" in chamada.kwargs and "Criar nova senha" in chamada.kwargs["html"]

        row = db.query(PasswordResetToken).one()
        db.refresh(row)
        assert row.token is None                      # nada em texto puro
        assert row.token_hash == hash_reset_token(token)
        assert row.sent_at is not None and row.delivery_attempts == 1 and row.delivery_error is None

        # o link do e-mail funciona uma vez
        payload = {"token": token, "new_password": NOVA, "password_confirmation": NOVA}
        assert http_unauth.post(AUTH + "/reset-password", json=payload).status_code == 200
        db.refresh(user)
        assert verify_password(NOVA, user.password_hash)
        assert http_unauth.post(AUTH + "/reset-password", json=payload).status_code == 400

    def test_resposta_indistinguivel(self, http_unauth, db, smtp):
        _user(db)
        _user(db, email="inativo@test.local", active=False)
        _user(db, email="cliente@test.local", role=UserRole.CLIENT)
        respostas = [
            _pedir(http_unauth, e) for e in (EMAIL, "nao@existe.com", "inativo@test.local", "cliente@test.local")
        ]
        assert {r.status_code for r in respostas} == {200}
        assert len({r.text for r in respostas}) == 1
        assert smtp.call_count == 1  # só a conta elegível recebe
        assert db.query(PasswordResetToken).count() == 1

    def test_smtp_nao_configurado_nao_muda_a_resposta(self, http_unauth, db, smtp, caplog):
        _user(db)
        smtp.side_effect = EmailConfigError("SMTP não configurado")
        with caplog.at_level(logging.INFO):
            r = _pedir(http_unauth)
        assert r.status_code == 200 and r.json()["message"] == reset_service.MENSAGEM_GENERICA
        row = db.query(PasswordResetToken).one()
        db.refresh(row)
        assert row.sent_at is None and row.delivery_error == "smtp_nao_configurado"
        assert "SMTP não está configurado" in caplog.text

    def test_falha_transitoria_tenta_de_novo(self, http_unauth, db, smtp):
        _user(db)
        smtp.side_effect = [smtplib.SMTPServerDisconnected("caiu"), None]
        _pedir(http_unauth)
        row = db.query(PasswordResetToken).one()
        db.refresh(row)
        assert smtp.call_count == 2 and row.delivery_attempts == 2
        assert row.sent_at is not None and row.delivery_error is None
        assert smtp.dormir.called

    def test_falha_persistente_registra_sem_segredo(self, http_unauth, db, smtp, caplog, monkeypatch):
        monkeypatch.setattr(settings, "password_reset_email_attempts", 3)
        _user(db)
        smtp.side_effect = smtplib.SMTPException("indisponível")
        with caplog.at_level(logging.DEBUG):
            _pedir(http_unauth)
        row = db.query(PasswordResetToken).one()
        db.refresh(row)
        assert smtp.call_count == 3 and row.delivery_attempts == 3
        assert row.sent_at is None and row.delivery_error == "SMTPException"
        token = _token_do_email(smtp.call_args)
        assert token not in caplog.text and "resetar-senha" not in caplog.text


class TestTokens:
    def _emitir(self, db, user, minutos=10, token="tok-fixo"):
        db.add(PasswordResetToken(
            user_id=user.id, token_hash=hash_reset_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutos),
        ))
        db.commit()
        return token

    def test_expirado(self, http_unauth, db):
        token = self._emitir(db, _user(db), minutos=-1)
        r = http_unauth.post(AUTH + "/reset-password", json={
            "token": token, "new_password": NOVA, "password_confirmation": NOVA})
        assert r.status_code == 400 and "expirado" in r.json()["detail"]

    def test_inexistente(self, http_unauth, db):
        r = http_unauth.post(AUTH + "/reset-password", json={
            "token": "nunca-emitido", "new_password": NOVA, "password_confirmation": NOVA})
        assert r.status_code == 400

    def test_conta_desativada_depois_do_pedido_nao_reativa(self, http_unauth, db):
        user = _user(db)
        token = self._emitir(db, user)
        user.is_deleted = True
        db.commit()
        r = http_unauth.post(AUTH + "/reset-password", json={
            "token": token, "new_password": NOVA, "password_confirmation": NOVA})
        assert r.status_code == 400
        db.refresh(user)
        assert user.is_deleted and verify_password("Antiga#123", user.password_hash)

    def test_novo_pedido_anula_o_anterior(self, http_unauth, db, smtp):
        _user(db)
        _pedir(http_unauth)
        primeiro = _token_do_email(smtp.call_args)
        _pedir(http_unauth)
        segundo = _token_do_email(smtp.call_args)
        r1 = http_unauth.post(AUTH + "/reset-password", json={
            "token": primeiro, "new_password": NOVA, "password_confirmation": NOVA})
        assert r1.status_code == 400
        r2 = http_unauth.post(AUTH + "/reset-password", json={
            "token": segundo, "new_password": NOVA, "password_confirmation": NOVA})
        assert r2.status_code == 200


class TestLimites:
    def test_limite_por_conta_nao_emite_nem_anula(self, http_unauth, db, smtp, monkeypatch):
        monkeypatch.setattr(settings, "password_reset_max_per_window", 3)
        _user(db)
        respostas = [_pedir(http_unauth) for _ in range(5)]
        assert len({r.text for r in respostas}) == 1
        assert smtp.call_count == 3
        ultimo_enviado = _token_do_email(smtp.call_args)
        # o 4º e o 5º pedidos não anularam o link que o usuário recebeu
        r = http_unauth.post(AUTH + "/reset-password", json={
            "token": ultimo_enviado, "new_password": NOVA, "password_confirmation": NOVA})
        assert r.status_code == 200

    def test_limite_por_origem_declarado(self):
        from app.api.v1.endpoints.auth import forgot_password
        chave = f"{forgot_password.__module__}.{forgot_password.__name__}"
        limites = [str(l.limit) for l in limiter._route_limits.get(chave, [])]
        assert limites, "forgot-password sem limite por IP"


class TestSemSegredoEmLog:
    def test_fluxo_completo_nao_loga_token_link_nem_senha(self, http_unauth, db, smtp, caplog):
        _user(db)
        with caplog.at_level(logging.DEBUG):
            _pedir(http_unauth)
            token = _token_do_email(smtp.call_args)
            http_unauth.post(AUTH + "/reset-password", json={
                "token": token, "new_password": NOVA, "password_confirmation": NOVA})
        assert token not in caplog.text
        assert NOVA not in caplog.text
        assert "resetar-senha?token" not in caplog.text


# ---------------------------------------------------------------------------
# PostgreSQL: dois resets simultâneos com o mesmo token
# ---------------------------------------------------------------------------

@pytest.mark.postgres
def test_consumo_concorrente_do_token_no_postgres(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL não configurada")
    schema = f"test_reset_{uuid4().hex}"
    admin = create_engine(url, future=True)
    with admin.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, future=True, connect_args={"options": f"-csearch_path={schema}"})
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
        with sessions() as s:
            user = _user(s)
            s.add(PasswordResetToken(user_id=user.id, token_hash=hash_reset_token("tok-pg"),
                                     expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
            s.commit()
            user_id = user.id

        barreira = Barrier(2, timeout=15)
        elegivel = reset_service._elegivel

        def elegivel_com_barreira(u):
            ok = elegivel(u)
            barreira.wait()  # os dois leram "não usado"; agora disputam o UPDATE
            return ok

        monkeypatch.setattr(reset_service, "_elegivel", elegivel_com_barreira)

        def resetar(senha):
            tc = TestClient(app, raise_server_exceptions=False)
            return tc.post(AUTH + "/reset-password", json={
                "token": "tok-pg", "new_password": senha, "password_confirmation": senha})

        with ThreadPoolExecutor(max_workers=2) as pool:
            respostas = list(pool.map(resetar, ["Primeira#2026", "Segunda#2026"]))

        assert sorted(r.status_code for r in respostas) == [200, 400]
        vencedora = ["Primeira#2026", "Segunda#2026"][[r.status_code for r in respostas].index(200)]
        with sessions() as s:
            assert verify_password(vencedora, s.get(User, user_id).password_hash)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
        with admin.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
