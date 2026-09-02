"""
Testes para app.services.ailos_client.request() — retry de tokens, detecção
de "[99] ... processamento" e logging em ailos_api_logs.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.config import settings
from app.models.ailos_api_log import AilosApiLog
from app.services import ailos_client
from app.services.ailos_client import AilosApiError, AilosError, request


@pytest.fixture(autouse=True)
def _ailos_settings(monkeypatch):
    monkeypatch.setattr(settings, 'ailos_gateway_base_url', 'https://gateway.ailos.test')
    monkeypatch.setattr(settings, 'ailos_apim_base_url', 'https://apim.ailos.test')
    monkeypatch.setattr(settings, 'ailos_timeout_seconds', 5)
    monkeypatch.setattr(settings, 'ailos_developer_key', 'dev-key')


@pytest.fixture(autouse=True)
def _tokens(monkeypatch):
    """Bypass token persistence: feed canned tokens and track refresh calls."""
    tokens = {'client': 'client-token-1', 'cooperado': 'coop-token-1'}

    monkeypatch.setattr(ailos_client, 'get_valid_client_token', lambda db: tokens['client'])
    monkeypatch.setattr(ailos_client, 'get_valid_cooperado_token', lambda db: tokens['cooperado'])

    def _refresh_client(db):
        tokens['client'] = 'client-token-2'
        return tokens['client']

    def _refresh_cooperado(db):
        tokens['cooperado'] = 'coop-token-2'
        return tokens['cooperado']

    monkeypatch.setattr(ailos_client, 'refresh_client_token', _refresh_client)
    monkeypatch.setattr(ailos_client, 'refresh_cooperado_token', _refresh_cooperado)
    return tokens


def _resp(status_code=200, json_data=None, text='', headers=None, content=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers if headers is not None else {'Content-Type': 'application/json'}
    resp.text = text
    resp.content = content if content is not None else text.encode()
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError('no json')
    return resp


class TestRequestRetry:
    def test_401_wso2_refreshes_client_token_and_retries(self, db):
        responses = [
            _resp(401, headers={'WWW-Authenticate': 'Bearer realm="WSO2"'}, text=''),
            _resp(200, json_data={'ok': True}),
        ]
        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.side_effect = responses
            result = request(db, 'GET', '/v1/test')

        assert result.status_code == 200
        assert result.json == {'ok': True}
        assert mock_requests.request.call_count == 2

        _, kwargs2 = mock_requests.request.call_args_list[1]
        assert kwargs2['headers']['Authorization'] == 'Bearer client-token-2'

    def test_401_empty_body_refreshes_cooperado_token_and_retries(self, db):
        responses = [
            _resp(401, headers={}, text='', content=b''),
            _resp(200, json_data={'ok': True}),
        ]
        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.side_effect = responses
            result = request(db, 'GET', '/v1/test')

        assert result.status_code == 200
        assert mock_requests.request.call_count == 2

        _, kwargs2 = mock_requests.request.call_args_list[1]
        assert kwargs2['headers']['x-ailos-authentication'] == 'Bearer coop-token-2'

    def test_400_processing_message_is_not_an_error(self, db):
        body = {'mensagem': '[99] Solicitação em processamento'}
        resp = _resp(400, json_data=body, text=str(body))

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = resp
            result = request(db, 'GET', '/v1/test')

        assert result.processing is True
        assert result.status_code == 400
        assert mock_requests.request.call_count == 1

    def test_400_generic_error_raises_and_logs(self, db):
        body = {'mensagem': 'Dados inválidos', 'codigo': 'E001'}
        resp = _resp(400, json_data=body, text=str(body))

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = resp
            with pytest.raises(AilosApiError) as excinfo:
                request(db, 'POST', '/v1/test', json_body={'a': 1})

        assert excinfo.value.status_code == 400
        assert excinfo.value.ailos_code == 'E001'

        log = db.query(AilosApiLog).first()
        assert log is not None
        assert log.success is False
        assert log.status_code == 400
        assert log.endpoint == '/v1/test'
        assert log.method == 'POST'

    def test_persistent_401_raises_after_max_attempts(self, db):
        responses = [
            _resp(401, headers={'WWW-Authenticate': 'Bearer realm="WSO2"'}, text=''),
            _resp(401, headers={}, text='', content=b''),
            _resp(401, headers={}, text='', content=b''),
        ]
        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.side_effect = responses
            with pytest.raises(AilosApiError) as excinfo:
                request(db, 'GET', '/v1/test')

        assert excinfo.value.status_code == 401
        assert mock_requests.request.call_count == 3


class TestRequestConnectionFailure:
    """`request()` (chamadas de negócio: gerar/consultar boleto etc.) tem o
    mesmo try/except de conexão que `fetch_client_token` (coberto em
    test_ailos_client_token.py::test_connection_error_raises_ailos_error),
    mas nunca foi exercitado neste caminho — timeout e erro de conexão viram
    exceções não tratadas se o wrapping quebrar aqui sem ninguém notar."""

    def test_timeout_raises_ailos_error_without_retry(self, db):
        import requests as real_requests

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.RequestException = real_requests.RequestException
            mock_requests.request.side_effect = real_requests.Timeout('Read timed out')
            with pytest.raises(AilosError) as excinfo:
                request(db, 'GET', '/v1/test')

        assert 'Falha de conexão' in str(excinfo.value)
        # erro de transporte não é 401 — não há por que tentar de novo
        assert mock_requests.request.call_count == 1

    def test_connection_error_is_logged_with_no_status_code(self, db):
        import requests as real_requests

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.RequestException = real_requests.RequestException
            mock_requests.request.side_effect = real_requests.ConnectionError('refused')
            with pytest.raises(AilosError):
                request(db, 'POST', '/v1/test', json_body={'a': 1}, billing_id=None)

        log = db.query(AilosApiLog).first()
        assert log is not None
        assert log.success is False
        assert log.status_code is None
        assert log.endpoint == '/v1/test'
        assert 'refused' in (log.error_message or '')
