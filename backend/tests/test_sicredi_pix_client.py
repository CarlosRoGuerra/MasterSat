"""Contrato HTTP, mTLS, token e bloqueio de escrita do canal Pix."""
from types import SimpleNamespace

import pytest
import requests

from app.core.config import settings
from app.services import sicredi_pix_client as api


class Response:
    def __init__(self, status, body):
        self.status_code = status
        self.body = body

    def json(self):
        return self.body


@pytest.fixture()
def transport(monkeypatch, tmp_path):
    path = tmp_path / 'cert.pem'
    path.write_text('PEM apenas para testar passagem ao transporte falso')
    for key, value in {
        'sicredi_pix_enabled': True, 'sicredi_pix_ambiente': 'homologacao',
        'sicredi_pix_client_id': 'id-teste', 'sicredi_pix_client_secret': 'segredo-privado',
        'sicredi_pix_cert_path': str(path), 'sicredi_pix_key_path': str(path), 'sicredi_pix_ca_path': str(path),
        'sicredi_pix_escrita_habilitada': False, 'sicredi_pix_producao_confirmada': False,
    }.items():
        monkeypatch.setattr(settings, key, value)
    calls = []
    responses = []
    session = SimpleNamespace(headers={}, cert=None, verify=None, trust_env=True, close=lambda: None)

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith('/oauth/token'):
            return Response(200, {'access_token': 'TOKEN-NAO-LOGAR', 'expires_in': 300,
                                  'scope': kwargs['data']['scope']})
        item = responses.pop(0) if responses else Response(200, {'ok': True})
        if isinstance(item, Exception): raise item
        return item

    session.request = request
    monkeypatch.setattr(api.requests, 'Session', lambda: session)
    api._tokens.clear()
    yield session, calls, responses
    api._tokens.clear()


def test_autentica_com_formulario_basic_mtls_e_reutiliza_token(transport):
    session, calls, _ = transport
    with api.PixClient() as client:
        client.request('GET', '/api/v1/rec')
        client.request('GET', '/api/v1/cobr')
        assert session.cert[1] == settings.sicredi_pix_key_path
        assert session.verify and session.verify is not False
        assert session.trust_env is False
    auth = calls[0]
    assert auth[:2] == ('POST', 'https://api-pix-h.sicredi.com.br/oauth/token')
    assert auth[2]['auth'] == ('id-teste', 'segredo-privado')
    assert auth[2]['data']['grant_type'] == 'client_credentials'
    assert 'json' not in auth[2] and auth[2]['allow_redirects'] is False
    assert len([c for c in calls if c[1].endswith('/oauth/token')]) == 1
    assert calls[1][2]['headers']['Authorization'] == 'Bearer TOKEN-NAO-LOGAR'


def test_escrita_desativada_bloqueia_antes_de_qualquer_rede(transport):
    _, calls, _ = transport
    with pytest.raises(api.PixError, match='desativada'):
        api.PixClient(escrita=True)
    with api.PixClient() as client, pytest.raises(api.PixError, match='somente consultas'):
        client.request('POST', '/api/v1/rec', json={})
    assert not calls


def test_producao_exige_liberacao_independente(transport, monkeypatch):
    _, calls, _ = transport
    monkeypatch.setattr(settings, 'sicredi_pix_escrita_habilitada', True)
    monkeypatch.setattr(settings, 'sicredi_pix_ambiente', 'producao')
    with pytest.raises(api.PixError, match='produção'):
        api.PixClient(escrita=True)
    assert calls == []
    with api.PixClient() as client:
        client.request('GET', '/api/v1/rec')
    assert calls[0][1] == 'https://api-pix.sicredi.com.br/oauth/token'


def test_401_renova_apenas_consulta_get(transport):
    _, calls, responses = transport
    responses.extend([Response(401, {'title': 'negado'}), Response(200, {})])
    with api.PixClient() as client:
        client.request('GET', '/api/v1/rec')
    assert len(calls) == 4


def test_timeout_mutacao_nunca_reenvia(transport, monkeypatch):
    _, calls, responses = transport
    monkeypatch.setattr(settings, 'sicredi_pix_escrita_habilitada', True)
    responses.append(requests.Timeout('timeout'))
    with api.PixClient(escrita=True) as client, pytest.raises(api.PixResultadoIncerto):
        client.request('PUT', '/api/v1/cobr/' + 'a' * 32, json={'valor': {'original': '10.00'}})
    assert len([c for c in calls if c[0] == 'PUT']) == 1


def test_erro_do_banco_nao_expoe_credenciais_ou_documento(transport):
    _, _, responses = transport
    responses.append(Response(403, {'detail': 'segredo-privado id-teste 14228344000167'}))
    with api.PixClient() as client, pytest.raises(api.PixError) as raised:
        client.request('GET', '/api/v1/rec')
    assert 'segredo-privado' not in str(raised.value)
    assert '14228344000167' not in str(raised.value)
