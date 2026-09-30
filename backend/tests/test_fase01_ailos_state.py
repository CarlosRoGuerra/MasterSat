"""FIN-09 — state do OAuth Ailos com prazo e consumo único; renovação do
token do cooperado serializada.

Nenhuma chamada real à Ailos: requests é mockado. O contrato do callback
não muda (o campo ``code`` continua sendo o token do cooperado).
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.crypto import decrypt_token, encrypt_token
from app.db.session import Base
from app.models.ailos_client_token import AilosClientToken
from app.models.ailos_integration import AilosIntegration
from app.services import ailos_client
from app.services.ailos_client import AilosError, build_cooperado_login_url, handle_cooperado_callback

CHAVE = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _ailos(monkeypatch):
    monkeypatch.setattr(settings, "ailos_client_id", "client-id")
    monkeypatch.setattr(settings, "ailos_client_secret", "client-secret")
    monkeypatch.setattr(settings, "ailos_apim_base_url", "https://apim.ailos.invalid")
    monkeypatch.setattr(settings, "ailos_callback_url", "https://backend.invalid/api/v1/ailos/callback")
    monkeypatch.setattr(settings, "ailos_env", "sandbox")
    monkeypatch.setattr(settings, "ailos_token_encryption_key", CHAVE)
    monkeypatch.setattr(settings, "ailos_state_ttl_minutes", 15)


def _integracao(db, state="st-1", minutos=10, **extra):
    integ = AilosIntegration(
        numero_convenio="102004", codigo_carteira=1, status="pending", state=state,
        state_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=minutos)) if minutos is not None else None,
        **extra,
    )
    db.add(integ)
    db.commit()
    db.refresh(integ)
    return integ


def _client_token(db):
    db.add(AilosClientToken(
        environment=settings.ailos_env,
        access_token_encrypted=encrypt_token("app-token"),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    db.commit()


class TestState:
    def test_state_valido_autoriza_uma_vez(self, db):
        _integracao(db)
        integ = handle_cooperado_callback(db, "st-1", "token-coop")
        assert integ.status == "authorized" and integ.state is None and integ.state_expires_at is None
        assert decrypt_token(integ.cooperado_token_encrypted) == "token-coop"
        with pytest.raises(AilosError):
            handle_cooperado_callback(db, "st-1", "token-replay")
        db.refresh(integ)
        assert decrypt_token(integ.cooperado_token_encrypted) == "token-coop"

    def test_state_expirado_e_recusado_e_descartado(self, db):
        integ = _integracao(db, minutos=-1)
        with pytest.raises(AilosError, match="expirado"):
            handle_cooperado_callback(db, "st-1", "token-tarde")
        db.refresh(integ)
        assert integ.cooperado_token_encrypted is None and integ.status == "pending"
        assert integ.state is None  # não fica reutilizável

    def test_state_legado_sem_prazo_conta_como_expirado(self, db):
        _integracao(db, minutos=None)
        with pytest.raises(AilosError):
            handle_cooperado_callback(db, "st-1", "token")

    def test_state_vazio_recusado(self, db):
        _integracao(db)
        with pytest.raises(AilosError):
            handle_cooperado_callback(db, "", "token")

    def test_novo_connect_invalida_o_state_anterior(self, db):
        _client_token(db)
        resp = MagicMock(status_code=200, text="COOP", headers={"Content-Type": "text/plain"})
        resp.json.side_effect = ValueError()
        with patch("app.services.ailos_client.requests") as req:
            req.post.return_value = resp
            _, primeiro = build_cooperado_login_url(db)
            _, segundo = build_cooperado_login_url(db)
        assert primeiro != segundo
        with pytest.raises(AilosError):
            handle_cooperado_callback(db, primeiro, "token-velho")
        assert handle_cooperado_callback(db, segundo, "token-novo").status == "authorized"

    def test_endpoint_publico_recusa_expirado_sem_gravar(self, http_unauth, db):
        integ = _integracao(db, minutos=-5)
        r = http_unauth.post("/api/v1/ailos/callback", json={"state": "st-1", "code": "token-x"})
        assert r.status_code == 400
        assert "token-x" not in r.text
        db.refresh(integ)
        assert integ.cooperado_token_encrypted is None


class TestRenovacaoDeduplicada:
    def test_renovacao_recente_nao_chama_a_ailos_de_novo(self, db):
        _client_token(db)
        _integracao(
            db, state=None, minutos=None, cooperado_token_encrypted=encrypt_token("tok-atual"),
            cooperado_token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=29),
            last_refresh_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        )
        db.query(AilosIntegration).update({"status": "authorized"})
        db.commit()
        with patch("app.services.ailos_client.requests") as req:
            assert ailos_client.refresh_cooperado_token(db) == "tok-atual"
        req.get.assert_not_called()

    def test_renovacao_antiga_chama_a_ailos(self, db):
        _client_token(db)
        _integracao(
            db, state=None, minutos=None, cooperado_token_encrypted=encrypt_token("tok-atual"),
            cooperado_token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            last_refresh_at=datetime.now(timezone.utc) - timedelta(minutes=15),
        )
        db.query(AilosIntegration).update({"status": "authorized"})
        db.commit()
        resp = MagicMock(status_code=200, text='"tok-novo"', headers={"Content-Type": "application/json"})
        resp.json.return_value = "tok-novo"
        with patch("app.services.ailos_client.requests") as req:
            req.get.return_value = resp
            assert ailos_client.refresh_cooperado_token(db) == "tok-novo"
        req.get.assert_called_once()


# ---------------------------------------------------------------------------
# PostgreSQL real
# ---------------------------------------------------------------------------

@pytest.fixture()
def pg():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL não configurada")
    schema = f"test_ailos_{uuid4().hex}"
    admin = create_engine(url, future=True)
    with admin.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, future=True, connect_args={"options": f"-csearch_path={schema}"})
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, autoflush=False, autocommit=False)
    finally:
        engine.dispose()
        with admin.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.mark.postgres
class TestConcorrenciaPostgres:
    def test_dois_callbacks_com_o_mesmo_state(self, pg, monkeypatch):
        with pg() as s:
            _integracao(s, state="st-pg")

        barreira = Barrier(2, timeout=15)
        update_original = ailos_client.update

        def update_com_barreira(*a, **k):
            barreira.wait()  # os dois já leram a linha com o state
            return update_original(*a, **k)

        monkeypatch.setattr(ailos_client, "update", update_com_barreira)

        def callback(code):
            with pg() as s:
                try:
                    handle_cooperado_callback(s, "st-pg", code)
                    return code
                except AilosError:
                    return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(callback, ["token-A", "token-B"]))

        vencedores = [r for r in resultados if r]
        assert len(vencedores) == 1, resultados
        with pg() as s:
            integ = s.query(AilosIntegration).one()
            assert decrypt_token(integ.cooperado_token_encrypted) == vencedores[0]
            assert integ.state is None

    def test_duas_renovacoes_simultaneas_chamam_a_ailos_uma_vez(self, pg):
        with pg() as s:
            _client_token(s)
            _integracao(
                s, state=None, minutos=None, cooperado_token_encrypted=encrypt_token("tok-atual"),
                cooperado_token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                last_refresh_at=datetime.now(timezone.utc) - timedelta(minutes=25),
            )
            s.query(AilosIntegration).update({"status": "authorized"})
            s.commit()

        chamadas = []

        def get_lento(*a, **k):
            chamadas.append(k.get("params"))
            time.sleep(0.5)
            resp = MagicMock(status_code=200, text='"tok-novo"', headers={"Content-Type": "application/json"})
            resp.json.return_value = "tok-novo"
            return resp

        barreira = Barrier(2, timeout=15)

        def renovar(_):
            barreira.wait()
            with pg() as s:
                return ailos_client.refresh_cooperado_token(s)

        with patch("app.services.ailos_client.requests") as req:
            req.get.side_effect = get_lento
            with ThreadPoolExecutor(max_workers=2) as pool:
                tokens = list(pool.map(renovar, range(2)))

        assert tokens == ["tok-novo", "tok-novo"]
        assert len(chamadas) == 1, chamadas
