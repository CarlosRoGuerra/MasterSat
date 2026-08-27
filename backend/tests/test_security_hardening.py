"""Testes das correções de segurança.

Cobre, na ordem da auditoria:
  1. Resolução do IP real do cliente (rate limit e auditoria eram forjáveis)
  2. Allowlist de MIME nos uploads (XSS armazenado)
  3. Serviço de documento força attachment para tipo não seguro
  4. Revogação de sessão na troca de senha
  5. .dockerignore protege o certificado ICP-Brasil
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from jose import jwt

from app.core.client_ip import client_ip_from_scope
from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token, token_revogado
from app.core.uploads import serving_content_type, validate_content_type
from app.models.enums import UserRole


def _scope(xff, peer="172.18.0.5"):
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    return {"type": "http", "headers": headers, "client": (peer, 12345)}


def _payload(token):
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])


def _token_com_iat(sub, emitido_em):
    """Token com 'iat' explicito — evita depender do relogio do teste.

    O corte de revogacao tem resolucao de SEGUNDO (o 'iat' do JWT e um
    timestamp inteiro), entao um token emitido no mesmo segundo do reset
    fica na fronteira. Isso e proposital: e o que garante que o login
    imediatamente APOS a troca de senha nao seja invalidado. Nos testes,
    cunhamos o token com um iat claramente anterior.
    """
    return jwt.encode(
        {
            "sub": sub,
            "iat": emitido_em,
            "exp": emitido_em + timedelta(minutes=30),
            "type": "access",
        },
        settings.secret_key,
        algorithm=settings.algorithm,
    )


class _FakeUpload:
    def __init__(self, content_type):
        self.content_type = content_type
        self.filename = "x"


# ---------------------------------------------------------------------------
# 1. IP real do cliente
# ---------------------------------------------------------------------------

class TestClientIp:
    """O nginx ANEXA o IP real no fim do X-Forwarded-For; tudo à esquerda
    pode ter vindo forjado na requisição do cliente."""

    def test_usa_ultima_entrada_do_xff(self):
        assert client_ip_from_scope(_scope("203.0.113.9")) == "203.0.113.9"

    def test_ignora_entrada_forjada_pelo_cliente(self):
        ip = client_ip_from_scope(_scope("8.8.8.8, 203.0.113.9"))
        assert ip == "203.0.113.9", "pegou o valor forjado pelo cliente"

    def test_cadeia_longa_forjada(self):
        ip = client_ip_from_scope(_scope("1.1.1.1, 2.2.2.2, 3.3.3.3, 203.0.113.9"))
        assert ip == "203.0.113.9"

    def test_sem_xff_usa_o_peer(self):
        assert client_ip_from_scope(_scope(None, peer="10.0.0.7")) == "10.0.0.7"

    def test_xff_vazio_usa_o_peer(self):
        assert client_ip_from_scope(_scope("  ", peer="10.0.0.7")) == "10.0.0.7"


# ---------------------------------------------------------------------------
# 2. Allowlist de MIME
# ---------------------------------------------------------------------------

class TestAllowlistMime:
    @pytest.mark.parametrize("tipo", ["application/pdf", "image/jpeg", "image/png", "image/webp"])
    def test_aceita_tipos_de_documento(self, tipo):
        assert validate_content_type(_FakeUpload(tipo)) == tipo

    @pytest.mark.parametrize("tipo", [
        "text/html",
        "image/svg+xml",
        "application/xhtml+xml",
        "text/javascript",
        "application/octet-stream",
    ])
    def test_recusa_tipo_ativo(self, tipo):
        with pytest.raises(HTTPException) as exc:
            validate_content_type(_FakeUpload(tipo))
        assert exc.value.status_code == 415

    def test_normaliza_charset_e_caixa(self):
        assert validate_content_type(_FakeUpload("APPLICATION/PDF; charset=utf-8")) == "application/pdf"

    def test_content_type_ausente_recusado(self):
        with pytest.raises(HTTPException):
            validate_content_type(_FakeUpload(None))


# ---------------------------------------------------------------------------
# 3. Serviço de documento
# ---------------------------------------------------------------------------

class TestServingContentType:
    """Rede de segurança para documentos salvos ANTES da allowlist existir."""

    def test_pdf_continua_inline(self):
        assert serving_content_type("application/pdf") == ("application/pdf", "inline")

    def test_html_legado_vira_attachment(self):
        media, disp = serving_content_type("text/html")
        assert disp == "attachment"
        assert media == "application/octet-stream"

    def test_svg_legado_vira_attachment(self):
        media, disp = serving_content_type("image/svg+xml")
        assert disp == "attachment"
        assert media == "application/octet-stream"

    def test_content_type_nulo_vira_attachment(self):
        assert serving_content_type(None) == ("application/octet-stream", "attachment")


# ---------------------------------------------------------------------------
# 4. Revogação de sessão
# ---------------------------------------------------------------------------

class TestRevogacaoDeSessao:
    def test_sem_corte_o_token_vale(self):
        assert token_revogado(_payload(create_access_token("1")), None) is False

    def test_token_emitido_antes_do_corte_e_recusado(self):
        p = _payload(create_access_token("1"))
        corte = datetime.now(timezone.utc) + timedelta(seconds=5)
        assert token_revogado(p, corte) is True

    def test_token_emitido_depois_do_corte_vale(self):
        corte = datetime.now(timezone.utc) - timedelta(hours=1)
        assert token_revogado(_payload(create_access_token("1")), corte) is False

    def test_refresh_token_tambem_carrega_iat(self):
        """O refresh vive 7 dias — é o que mais importa revogar."""
        token, _jti, _family = create_refresh_token("1")
        p = _payload(token)
        assert "iat" in p
        corte = datetime.now(timezone.utc) + timedelta(seconds=5)
        assert token_revogado(p, corte) is True

    def test_token_legado_sem_iat_e_recusado_quando_ha_corte(self):
        assert token_revogado({"sub": "1"}, datetime.now(timezone.utc)) is True

    def test_corte_naive_tratado_como_utc(self):
        """SQLite devolve datetime sem tzinfo; não pode estourar TypeError."""
        p = _payload(create_access_token("1"))
        corte = (datetime.now(timezone.utc) + timedelta(seconds=5)).replace(tzinfo=None)
        assert token_revogado(p, corte) is True


class TestResetPasswordDerrubaSessao:
    def test_reset_grava_o_corte_e_invalida_o_token_antigo(self, db, http_unauth):
        from app.core.security import get_password_hash
        from app.models.password_reset_token import PasswordResetToken
        from app.models.user import User

        user = User(
            name="Alvo", email="alvo@test.local", role=UserRole.ADMIN,
            active=True, is_deleted=False,
            password_hash=get_password_hash("senha-antiga"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        assert user.tokens_valid_from is None

        # Emitido um minuto antes do reset — fora da fronteira de 1s.
        token_antigo = _token_com_iat(
            str(user.id), datetime.now(timezone.utc) - timedelta(minutes=1)
        )

        db.add(PasswordResetToken(
            user_id=user.id, token="tok-reset",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        ))
        db.commit()

        nova = "SenhaNova#123"
        resp = http_unauth.post("/api/v1/auth/reset-password", json={
            "token": "tok-reset",
            "new_password": nova,
            "password_confirmation": nova,
        })
        assert resp.status_code == 200, resp.text

        db.refresh(user)
        assert user.tokens_valid_from is not None, "reset nao gravou o corte"
        assert token_revogado(_payload(token_antigo), user.tokens_valid_from) is True

        # E o token emitido DEPOIS do reset continua valendo — senao o usuario
        # nao conseguiria entrar com a senha nova.
        token_novo = _token_com_iat(
            str(user.id), datetime.now(timezone.utc) + timedelta(seconds=2)
        )
        assert token_revogado(_payload(token_novo), user.tokens_valid_from) is False


# ---------------------------------------------------------------------------
# 5. O .dockerignore protege o certificado
# ---------------------------------------------------------------------------

class TestDockerignore:
    """O .gitignore NAO tem efeito sobre o Docker: sem .dockerignore, o
    "COPY . ." leva o .pfx e os tokens da Ailos para dentro da imagem."""

    @property
    def _linhas(self):
        caminho = Path(__file__).resolve().parents[1] / ".dockerignore"
        assert caminho.exists(), "backend/.dockerignore sumiu"
        return {
            linha.strip()
            for linha in caminho.read_text(encoding="utf-8").splitlines()
            if linha.strip() and not linha.startswith("#")
        }

    @pytest.mark.parametrize("padrao", [
        "certs/",
        "*.pfx",
        "*.p12",
        ".env",
        "scripts/ailos_cooperado_jwt.json",
        "scripts/ailos_login_gerado.json",
    ])
    def test_segredo_excluido_do_contexto_de_build(self, padrao):
        assert padrao in self._linhas, f"{padrao} entraria na imagem Docker"

    def test_dockerfile_nao_roda_como_root(self):
        dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
        assert "USER appuser" in dockerfile.read_text(encoding="utf-8")
