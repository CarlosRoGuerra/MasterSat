"""
Testes de app.services.sgr_migration.client.SGRClient — autenticação,
paginação/params, diferenciação de erros HTTP (ETAPA 15) e contagem de
requisições (ETAPA 14). A sessão HTTP é substituída por um dublê em memória:
nenhum destes testes faz chamada de rede real.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from app.core.config import settings
from app.services.sgr_migration.client import (
    SGRApiError,
    SGRAuthenticationError,
    SGRClient,
    SGRConnectionError,
    SGRError,
    SGRInvalidResponseError,
    SGRNotFoundError,
    SGRRateLimitError,
    SGRServerError,
)


@pytest.fixture(autouse=True)
def _sgr_settings(monkeypatch):
    monkeypatch.setattr(settings, 'sgr_base_url', 'https://sgr.test/service')
    monkeypatch.setattr(settings, 'sgr_cod_mobile', '1234')
    monkeypatch.setattr(settings, 'sgr_username', 'usuario-teste')
    monkeypatch.setattr(settings, 'sgr_password', 'senha-teste')
    monkeypatch.setattr(settings, 'sgr_api_key', 'chave-teste')
    monkeypatch.setattr(settings, 'sgr_timeout_seconds', 5)


class FakeSession:
    """Dublê de requests.Session — devolve as respostas na ordem dada."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, **kwargs):
        self.calls.append({'method': method, 'url': url, **kwargs})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _resp(status_code=200, json_data=None, headers=None, text='', json_raises=False):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.text = text
    if json_raises:
        resp.json.side_effect = ValueError('not json')
    else:
        resp.json.return_value = json_data if json_data is not None else {}
    return resp


# O comportamento real da API (confirmado rodando a POC contra o SGR de
# verdade) é devolver X-Auth-Token/Authorization DENTRO do corpo JSON, num
# objeto "Headers" — não como headers HTTP. Ver nota em client.authenticate().
def _auth_resp(token='tok-1', authorization='auth-1'):
    return _resp(200, {'Error': 'false', 'Headers': {'X-Auth-Token': token, 'Authorization': authorization}})


class TestConfig:
    def test_missing_credentials_raise_without_network_call(self, monkeypatch):
        monkeypatch.setattr(settings, 'sgr_api_key', '')
        session = FakeSession([])
        client = SGRClient(session=session)
        with pytest.raises(SGRError, match='SGR_API_KEY'):
            client.authenticate()
        assert session.calls == []


class TestAuthentication:
    def test_success_sets_headers_and_counts_one_request(self):
        session = FakeSession([_auth_resp()])
        client = SGRClient(session=session)
        client.authenticate()
        assert client.is_authenticated is True
        assert client.request_count == 1

    def test_sends_credentials_in_json_body_not_url(self):
        session = FakeSession([_auth_resp()])
        client = SGRClient(session=session)
        client.authenticate()
        call = session.calls[0]
        assert call['json'] == {'cliente': '1234', 'nome': 'usuario-teste', 'senha': 'senha-teste'}
        assert 'senha-teste' not in call['url']
        assert 'chave-teste' not in call['url']

    def test_missing_auth_headers_raises_invalid_response(self):
        session = FakeSession([_resp(200, {'Error': 'false'}, headers={})])
        client = SGRClient(session=session)
        with pytest.raises(SGRInvalidResponseError):
            client.authenticate()

    def test_falls_back_to_real_http_headers_if_body_has_no_headers_object(self):
        # Defensivo: se um dia a API passar a devolver headers HTTP de verdade
        # em vez do objeto "Headers" no corpo, o client continua funcionando.
        session = FakeSession([
            _resp(200, {'Error': 'false'}, headers={'X-Auth-Token': 'tok-1', 'Authorization': 'auth-1'}),
        ])
        client = SGRClient(session=session)
        client.authenticate()
        assert client.is_authenticated is True

    def test_401_on_auth_raises_authentication_error(self):
        session = FakeSession([_resp(401, {}, headers={})])
        client = SGRClient(session=session)
        with pytest.raises(SGRAuthenticationError):
            client.authenticate()

    def test_401_with_msg_body_surfaces_real_reason(self):
        # Confirmado contra a API real: um 401 pode não ter nada a ver com
        # credencial errada (ex.: "Restrição de data: você não tem permissão
        # para acessar o sistema hoje"). Sem isso a causa real fica escondida.
        session = FakeSession([
            _resp(401, {'error': True, 'msg': 'Restrição de data: você não tem permissão para acessar o sistema hoje.'}),
        ])
        client = SGRClient(session=session)
        with pytest.raises(SGRAuthenticationError, match='Restrição de data'):
            client.authenticate()

    def test_get_triggers_authentication_lazily(self):
        session = FakeSession([_auth_resp(), _resp(200, {'Error': 'false', 'Data': []})])
        client = SGRClient(session=session)
        client.get('/buscar_cliente', {'total': 1})
        assert client.is_authenticated is True
        assert session.calls[0]['url'].endswith('/headers_authorization')


class TestGetErrorHandling:
    def _client_with(self, *after_auth_responses):
        session = FakeSession([_auth_resp(), *after_auth_responses])
        return SGRClient(session=session), session

    def test_404_raises_not_found(self):
        client, _ = self._client_with(_resp(404))
        with pytest.raises(SGRNotFoundError):
            client.get('/buscar_cliente')

    def test_429_raises_rate_limit(self):
        client, _ = self._client_with(_resp(429))
        with pytest.raises(SGRRateLimitError):
            client.get('/buscar_cliente')

    def test_500_raises_server_error(self):
        client, _ = self._client_with(_resp(500))
        with pytest.raises(SGRServerError):
            client.get('/buscar_cliente')

    def test_other_4xx_raises_generic_api_error(self):
        client, _ = self._client_with(_resp(422))
        with pytest.raises(SGRApiError):
            client.get('/buscar_cliente')

    def test_timeout_raises_connection_error(self):
        session = FakeSession([_auth_resp(), requests.Timeout('boom')])
        client = SGRClient(session=session)
        with pytest.raises(SGRConnectionError):
            client.get('/buscar_cliente')

    def test_connection_failure_raises_connection_error(self):
        session = FakeSession([_auth_resp(), requests.ConnectionError('boom')])
        client = SGRClient(session=session)
        with pytest.raises(SGRConnectionError):
            client.get('/buscar_cliente')

    def test_invalid_json_raises_invalid_response(self):
        client, _ = self._client_with(_resp(200, json_raises=True))
        with pytest.raises(SGRInvalidResponseError):
            client.get('/buscar_cliente')

    def test_invalid_json_includes_body_preview_in_message(self):
        # Confirmado contra a API real: HTTP 200 com corpo HTML de erro
        # interno deles ("Can't connect to local MySQL server" — banco
        # deles fora do ar). Sem o trecho do corpo, a mensagem genérica
        # "não é JSON válido" escondia essa causa.
        corpo = "Error in exception handler: SQLSTATE[HY000] [2002] Can't connect to local MySQL server"
        client, _ = self._client_with(_resp(200, json_raises=True, text=corpo))
        with pytest.raises(SGRInvalidResponseError, match='MySQL'):
            client.get('/buscar_cliente')

    def test_invalid_json_without_body_does_not_crash(self):
        client, _ = self._client_with(_resp(200, json_raises=True, text=''))
        with pytest.raises(SGRInvalidResponseError):
            client.get('/buscar_cliente')

    def test_error_true_flag_raises_invalid_response(self):
        client, _ = self._client_with(_resp(200, {'Error': 'true'}))
        with pytest.raises(SGRInvalidResponseError):
            client.get('/buscar_cliente')

    def test_401_reauthenticates_once_and_retries(self):
        session = FakeSession([
            _auth_resp(),
            _resp(401, headers={}),
            _auth_resp(token='tok-2', authorization='auth-2'),
            _resp(200, {'Error': 'false', 'Data': []}),
        ])
        client = SGRClient(session=session)
        body = client.get('/buscar_cliente')
        assert body == {'Error': 'false', 'Data': []}
        assert client.request_count == 4

    def test_never_leaks_credentials_or_params_into_request_log(self):
        session = FakeSession([_auth_resp(), _resp(200, {'Error': 'false', 'Data': []})])
        client = SGRClient(session=session)
        client.get('/buscar_cliente', {'cpf_cliente': '72130708005', 'total': 1})
        for entry in client.request_log:
            assert entry.path in ('autenticação', '/buscar_cliente')
            assert '72130708005' not in str(entry.path)
            assert 'senha-teste' not in str(entry.path)


class TestPaginationParams:
    def test_buscar_clientes_sends_total_and_indice(self):
        session = FakeSession([_auth_resp(), _resp(200, {'Error': 'false', 'Data': []})])
        client = SGRClient(session=session)
        client.buscar_clientes(total=10, indice=0)
        assert session.calls[-1]['params'] == {'cliente': '1234', 'total': 10, 'indice': 0}

    def test_buscar_veiculos_por_cliente_filters_by_cod_cliente(self):
        session = FakeSession([_auth_resp(), _resp(200, {'Error': 'false', 'Data': []})])
        client = SGRClient(session=session)
        client.buscar_veiculos_por_cliente(cod_cliente=42)
        params = session.calls[-1]['params']
        assert params['cod_cliente'] == 42

    def test_buscar_vinculos_por_placa_filters_by_placa(self):
        session = FakeSession([_auth_resp(), _resp(200, {'Error': 'false', 'Data': []})])
        client = SGRClient(session=session)
        client.buscar_vinculos_por_placa('ABC1234')
        params = session.calls[-1]['params']
        assert params['placa_vinculo'] == 'ABC1234'

    def test_buscar_vendas_sends_cod_venda_and_ultima_atualizacao(self):
        session = FakeSession([_auth_resp(), _resp(200, {'Error': 'false', 'Data': []})])
        client = SGRClient(session=session)
        client.buscar_vendas(cod_venda=99, ultima_atualizacao='2020-01-01')
        params = session.calls[-1]['params']
        assert params['cod_venda'] == 99
        assert params['ultima_atualizacao'] == '2020-01-01'

    def test_get_vencimento_sends_cod_cliente(self):
        session = FakeSession([_auth_resp(), _resp(200, {'Error': 'false', 'Data': []})])
        client = SGRClient(session=session)
        client.get_vencimento(cod_cliente=1)
        assert session.calls[-1]['params']['cod_cliente'] == 1

    def test_buscar_boletos_cliente_limpa_a_mascara_do_cpf(self):
        # Com máscara o SGR devolve 0 boletos sem erro nenhum — o que já fez
        # parecer que clientes com 57 boletos não tinham nenhum.
        session = FakeSession([_auth_resp(), _resp(200, {'Error': 'false', 'Data': []})])
        client = SGRClient(session=session)
        client.buscar_boletos_cliente('063.234.889-57')
        assert session.calls[-1]['params']['cpf_cnpj'] == '06323488957'

    def test_buscar_boletos_cliente_repassa_filtros_de_data(self):
        session = FakeSession([_auth_resp(), _resp(200, {'Error': 'false', 'Data': []})])
        client = SGRClient(session=session)
        client.buscar_boletos_cliente('06323488957', data_emissao_inicio='01/01/2020')
        assert session.calls[-1]['params']['data_emissao_inicio'] == '01/01/2020'

    def test_buscar_boletos_abertos_cliente_limpa_a_mascara_do_cpf(self):
        session = FakeSession([_auth_resp(), _resp(200, {'Error': 'false', 'Data': []})])
        client = SGRClient(session=session)
        client.buscar_boletos_abertos_cliente('17.968.067/0001-17')
        assert session.calls[-1]['params']['cpf_cnpj'] == '17968067000117'

    def test_get_grupo_mensalidade_pagina(self):
        session = FakeSession([_auth_resp(), _resp(200, {'Error': 'false', 'Data': []})])
        client = SGRClient(session=session)
        client.get_grupo_mensalidade(total=50)
        assert session.calls[-1]['params']['total'] == 50

    def test_sends_cod_mobile_as_cliente_param_on_every_call(self):
        # Sem 'cliente=<codMobile>' na query, a API real responde 401 "Código
        # de cliente inválido" em TODOS os endpoints. Não está documentado em
        # endpoint nenhum da apidoc do fornecedor — e um header codMobile é
        # ignorado, só a query funciona.
        session = FakeSession([_auth_resp(), _resp(200, {'Error': 'false', 'Data': []})])
        client = SGRClient(session=session)
        client.buscar_clientes(total=10)
        assert session.calls[-1]['params']['cliente'] == '1234'

    def test_empty_params_are_omitted(self):
        session = FakeSession([_auth_resp(), _resp(200, {'Error': 'false', 'Data': []})])
        client = SGRClient(session=session)
        client.get('/buscar_cliente', {'cod_cliente': None, 'total': 10})
        assert session.calls[-1]['params'] == {'cliente': '1234', 'total': 10}


class TestExtractData:
    def test_list_data_passthrough(self):
        assert SGRClient.extract_data({'Data': [{'a': 1}]}) == [{'a': 1}]

    def test_single_dict_data_wrapped_in_list(self):
        assert SGRClient.extract_data({'Data': {'a': 1}}) == [{'a': 1}]

    def test_missing_data_returns_empty_list(self):
        assert SGRClient.extract_data({'Error': 'false'}) == []

    def test_lowercase_data_key_supported(self):
        assert SGRClient.extract_data({'data': [{'a': 1}]}) == [{'a': 1}]

    def test_invalid_data_type_raises(self):
        with pytest.raises(SGRInvalidResponseError):
            SGRClient.extract_data({'Data': 'not-a-list-or-dict'})
