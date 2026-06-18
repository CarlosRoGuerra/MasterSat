"""
Testes para a gestão do token de aplicação (client_credentials, APIM/WSO2)
em app.services.ailos_client.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.crypto import decrypt_token, encrypt_token
from app.models.ailos_client_token import AilosClientToken
from app.services.ailos_client import (
    AilosApiError,
    AilosError,
    fetch_client_token,
    get_valid_client_token,
)

VALID_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _ailos_settings(monkeypatch):
    monkeypatch.setattr(settings, 'ailos_client_id', 'client-id')
    monkeypatch.setattr(settings, 'ailos_client_secret', 'client-secret')
    monkeypatch.setattr(settings, 'ailos_apim_base_url', 'https://apim.ailos.test')
    monkeypatch.setattr(settings, 'ailos_env', 'sandbox')
    monkeypatch.setattr(settings, 'ailos_timeout_seconds', 5)
    monkeypatch.setattr(settings, 'ailos_token_encryption_key', VALID_KEY)


def _resp(status_code=200, json_data=None, text=''):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError('no json')
    return resp


class TestFetchClientToken:
    def test_success_persists_encrypted_token(self, db):
        resp = _resp(200, {
            'access_token': 'app-token-abc',
            'token_type': 'Bearer',
            'expires_in': 3600,
            'scope': 'boletos',
        })

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.post.return_value = resp
            row = fetch_client_token(db)

        assert row.environment == 'sandbox'
        assert row.token_type == 'Bearer'
        assert row.scope == 'boletos'
        assert row.expires_at is not None
        assert decrypt_token(row.access_token_encrypted) == 'app-token-abc'

        _, kwargs = mock_requests.post.call_args
        expected_basic = base64.b64encode(b'client-id:client-secret').decode('ascii')
        assert kwargs['headers']['Authorization'] == f'Basic {expected_basic}'
        assert kwargs['data'] == {'grant_type': 'client_credentials'}

    def test_upserts_existing_row_for_environment(self, db):
        existing = AilosClientToken(
            environment='sandbox',
            access_token_encrypted=encrypt_token('old-token'),
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db.add(existing)
        db.commit()
        existing_id = existing.id

        resp = _resp(200, {'access_token': 'new-token', 'expires_in': 1800})
        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.post.return_value = resp
            row = fetch_client_token(db)

        assert row.id == existing_id
        assert decrypt_token(row.access_token_encrypted) == 'new-token'
        assert db.query(AilosClientToken).count() == 1

    def test_missing_credentials_raises_ailos_error(self, db, monkeypatch):
        monkeypatch.setattr(settings, 'ailos_client_id', '')

        with pytest.raises(AilosError):
            fetch_client_token(db)

    def test_missing_apim_base_url_raises_ailos_error(self, db, monkeypatch):
        monkeypatch.setattr(settings, 'ailos_apim_base_url', '')

        with pytest.raises(AilosError):
            fetch_client_token(db)

    def test_http_error_raises_api_error(self, db):
        resp = _resp(401, text='invalid_client')

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.post.return_value = resp
            with pytest.raises(AilosApiError) as excinfo:
                fetch_client_token(db)

        assert excinfo.value.status_code == 401

    def test_missing_access_token_raises_api_error(self, db):
        resp = _resp(200, {'token_type': 'Bearer'})

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.post.return_value = resp
            with pytest.raises(AilosApiError):
                fetch_client_token(db)

    def test_connection_error_raises_ailos_error(self, db):
        import requests as real_requests

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.RequestException = real_requests.RequestException
            mock_requests.post.side_effect = real_requests.RequestException('boom')
            with pytest.raises(AilosError):
                fetch_client_token(db)


class TestGetValidClientToken:
    def test_fetches_when_no_token_row_exists(self, db):
        resp = _resp(200, {'access_token': 'fresh-token', 'expires_in': 3600})

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.post.return_value = resp
            token = get_valid_client_token(db)

        assert token == 'fresh-token'

    def test_returns_cached_token_when_still_valid(self, db):
        row = AilosClientToken(
            environment='sandbox',
            access_token_encrypted=encrypt_token('cached-token'),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(row)
        db.commit()

        with patch('app.services.ailos_client.requests') as mock_requests:
            token = get_valid_client_token(db)

        assert token == 'cached-token'
        mock_requests.post.assert_not_called()

    def test_refreshes_when_close_to_expiry(self, db):
        row = AilosClientToken(
            environment='sandbox',
            access_token_encrypted=encrypt_token('expiring-token'),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=10),
        )
        db.add(row)
        db.commit()

        resp = _resp(200, {'access_token': 'renewed-token', 'expires_in': 3600})
        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.post.return_value = resp
            token = get_valid_client_token(db)

        assert token == 'renewed-token'
