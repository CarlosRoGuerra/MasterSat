"""
Testes para o fluxo de autorização do cooperado (build_cooperado_login_url,
handle_cooperado_callback) e os endpoints públicos/admin /ailos/connect e
/ailos/callback.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.crypto import decrypt_token, encrypt_token
from app.models.ailos_client_token import AilosClientToken
from app.models.ailos_integration import AilosIntegration
from app.services.ailos_client import (
    AilosApiError,
    AilosError,
    build_cooperado_login_url,
    handle_cooperado_callback,
)

VALID_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _ailos_settings(monkeypatch):
    monkeypatch.setattr(settings, 'ailos_client_id', 'client-id')
    monkeypatch.setattr(settings, 'ailos_client_secret', 'client-secret')
    monkeypatch.setattr(settings, 'ailos_apim_base_url', 'https://apim.ailos.test')
    monkeypatch.setattr(settings, 'ailos_callback_url', 'https://backend.test/api/v1/ailos/callback')
    monkeypatch.setattr(settings, 'ailos_env', 'sandbox')
    monkeypatch.setattr(settings, 'ailos_timeout_seconds', 5)
    monkeypatch.setattr(settings, 'ailos_developer_key', 'dev-key')
    monkeypatch.setattr(settings, 'ailos_token_encryption_key', VALID_KEY)
    monkeypatch.setattr(settings, 'ailos_numero_convenio', '102004')


def _seed_client_token(db):
    row = AilosClientToken(
        environment=settings.ailos_env,
        access_token_encrypted=encrypt_token('app-token'),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(row)
    db.commit()
    return row


def _resp(status_code=200, text='', json_data=None, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.headers = headers if headers is not None else {}
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError('no json')
    return resp


class TestBuildCooperadoLoginUrl:
    def test_builds_login_url_and_saves_state(self, db):
        _seed_client_token(db)
        resp = _resp(200, text='COOP123', headers={'Content-Type': 'text/plain'})

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.post.return_value = resp
            login_url, state = build_cooperado_login_url(db)

        assert login_url == f'{settings.ailos_apim_base_url}/ailos/identity/api/v1/login/index?id=COOP123'

        _, post_kwargs = mock_requests.post.call_args
        assert post_kwargs['json']['state'] == state
        assert post_kwargs['json']['urlCallback'] == settings.ailos_callback_url
        assert post_kwargs['json']['ailosApiKeyDeveloper'] == settings.ailos_developer_key

        integration = db.query(AilosIntegration).first()
        assert integration is not None
        assert integration.state == state
        assert integration.status == 'pending'

    def test_missing_callback_url_raises_ailos_error(self, db, monkeypatch):
        monkeypatch.setattr(settings, 'ailos_callback_url', '')

        with pytest.raises(AilosError):
            build_cooperado_login_url(db)

    def test_obter_id_error_raises_api_error(self, db):
        _seed_client_token(db)
        resp = _resp(500, text='erro interno')

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.post.return_value = resp
            with pytest.raises(AilosApiError):
                build_cooperado_login_url(db)


class TestHandleCooperadoCallback:
    def test_valid_state_authorizes_and_encrypts_code(self, db):
        integration = AilosIntegration(
            numero_convenio=settings.ailos_numero_convenio,
            codigo_carteira=1,
            status='pending',
            state='abc-state-123',
        )
        db.add(integration)
        db.commit()

        result = handle_cooperado_callback(db, 'abc-state-123', 'auth-code-xyz')

        assert result.status == 'authorized'
        assert result.state is None
        assert result.authorized_at is not None
        assert decrypt_token(result.cooperado_token_encrypted) == 'auth-code-xyz'

    def test_invalid_state_raises_ailos_error(self, db):
        with pytest.raises(AilosError):
            handle_cooperado_callback(db, 'nonexistent-state', 'some-code')


class TestConnectEndpoint:
    def test_admin_can_connect(self, http, db):
        _seed_client_token(db)
        resp = _resp(200, text='COOP123', headers={'Content-Type': 'text/plain'})

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.post.return_value = resp
            response = http.post('/api/v1/ailos/connect')

        assert response.status_code == 200
        body = response.json()
        assert 'login_url' in body
        assert 'state' in body
        assert 'id=COOP123' in body['login_url']

    def test_financial_forbidden(self, http_fin):
        response = http_fin.post('/api/v1/ailos/connect')
        assert response.status_code == 403

    def test_unauthenticated_rejected(self, http_unauth):
        response = http_unauth.post('/api/v1/ailos/connect')
        assert response.status_code == 401


class TestCallbackEndpoint:
    def test_public_callback_success(self, http_unauth, db):
        integration = AilosIntegration(
            numero_convenio=settings.ailos_numero_convenio,
            codigo_carteira=1,
            status='pending',
            state='state-456',
        )
        db.add(integration)
        db.commit()

        response = http_unauth.post(
            '/api/v1/ailos/callback',
            json={'state': 'state-456', 'code': 'code-789'},
        )

        assert response.status_code == 200
        assert response.json() == {}
        assert 'code-789' not in response.text

    def test_invalid_state_returns_400(self, http_unauth, db):
        response = http_unauth.post(
            '/api/v1/ailos/callback',
            json={'state': 'wrong-state', 'code': 'code-789'},
        )

        assert response.status_code == 400
