"""Cliente HTTP da API de Cobrança Sicredi."""
from __future__ import annotations

import pytest
import requests

from app.core.config import settings
from app.models.sicredi_api_log import SicrediApiLog
from app.services import sicredi_client
from tests.sicredi_apoio import Resp, sicredi  # noqa: F401 — fixture

CONSULTA = '/cobranca/boleto/v1/boletos'


@pytest.mark.parametrize('campo', ['x-api-key', 'api_key', 'apiKey', 'Authorization', 'access_token'])
def test_mascara_chaves_e_tokens_em_relatorios(campo):
    segredo = 'segredo-de-teste-para-nao-publicar'
    resultado = sicredi_client.mask_payload({'headers': {campo: segredo}})
    assert segredo not in str(resultado)


def test_autentica_com_password_no_sandbox(sicredi):
    sicredi.quando('GET', CONSULTA, Resp(200, {'situacao': 'EM CARTEIRA'}))
    sicredi_client.request('GET', CONSULTA, params={'nossoNumero': '1'})
    token = sicredi.tokens[0]
    assert token['url'] == 'https://api-parceiro.sicredi.com.br/sb/auth/openapi/token'
    assert token['headers']['context'] == 'COBRANCA'
    assert token['headers']['x-api-key'] == settings.sicredi_api_key
    assert token['data'] == {'grant_type': 'password', 'username': '123456789',
                             'password': 'teste123', 'scope': 'cobranca'}
    headers = sicredi.chamadas[0][2]['headers']
    assert headers['Authorization'] == 'Bearer tok1'
    assert (headers['cooperativa'], headers['posto']) == ('6789', '03')


def test_token_e_reaproveitado_entre_chamadas(sicredi):
    sicredi.quando('GET', CONSULTA, Resp(200, {}))
    sicredi_client.request('GET', CONSULTA)
    sicredi_client.request('GET', CONSULTA)
    assert len(sicredi.tokens) == 1


def test_token_vencido_e_renovado_pelo_refresh(sicredi, monkeypatch):
    sicredi.quando('GET', CONSULTA, Resp(200, {}))
    sicredi_client.request('GET', CONSULTA)
    chave = next(iter(sicredi_client._TOKENS))
    sicredi_client._TOKENS[chave].expira_em = 0  # access vencido, refresh ainda vale
    sicredi_client.request('GET', CONSULTA)
    assert sicredi.tokens[1]['data'] == {'grant_type': 'refresh_token', 'refresh_token': 'ref1'}


def test_401_descarta_o_token_e_repete_uma_vez(sicredi):
    sicredi.quando('GET', CONSULTA, Resp(401, {'message': 'expirado'}), Resp(200, {'ok': True}))
    resp = sicredi_client.request('GET', CONSULTA)
    assert resp.json == {'ok': True}
    assert len(sicredi.tokens) == 2
    assert [c[2]['headers']['Authorization'] for c in sicredi.chamadas] == ['Bearer tok1', 'Bearer tok2']


def test_erro_http_vira_sicredi_api_error_com_a_mensagem(sicredi):
    sicredi.quando('POST', CONSULTA, Resp(422, {'codigo': 'E01', 'mensagem': 'Data de vencimento inválida'}))
    with pytest.raises(sicredi_client.SicrediApiError) as exc:
        sicredi_client.request('POST', CONSULTA, json_body={})
    assert exc.value.status_code == 422
    assert exc.value.mensagem == 'Data de vencimento inválida'
    assert exc.value.codigo == 'E01'


def test_timeout_depois_do_envio_e_desfecho_desconhecido(sicredi):
    sicredi.quando('POST', CONSULTA, requests.exceptions.ReadTimeout('read timed out'))
    with pytest.raises(sicredi_client.SicrediDesfechoDesconhecido):
        sicredi_client.request('POST', CONSULTA, json_body={})


def test_falha_de_conexao_antes_do_envio_nao_e_desfecho(sicredi):
    sicredi.quando('POST', CONSULTA, requests.exceptions.ConnectionError(
        'HTTPSConnectionPool: Max retries (Caused by NewConnectionError: Failed to establish a new connection)'
    ))
    with pytest.raises(sicredi_client.SicrediError) as exc:
        sicredi_client.request('POST', CONSULTA, json_body={})
    assert not isinstance(exc.value, sicredi_client.SicrediDesfechoDesconhecido)


def test_producao_sem_confirmacao_bloqueia_antes_de_qualquer_rede(sicredi, monkeypatch):
    monkeypatch.setattr(settings, 'sicredi_ambiente', 'producao')
    monkeypatch.setattr(settings, 'sicredi_codigo_acesso', 'CODIGO')
    with pytest.raises(sicredi_client.SicrediError, match='SICREDI_PRODUCAO_CONFIRMADA'):
        sicredi_client.request('GET', CONSULTA)
    assert sicredi.tokens == [] and sicredi.chamadas == []


def test_producao_usa_o_convenio_e_o_codigo_de_acesso_configurados(sicredi, monkeypatch):
    monkeypatch.setattr(settings, 'sicredi_ambiente', 'producao')
    monkeypatch.setattr(settings, 'sicredi_producao_confirmada', True)
    monkeypatch.setattr(settings, 'sicredi_codigo_acesso', 'CODIGO-DE-ACESSO')
    monkeypatch.setattr(settings, 'sicredi_cooperativa', '2602')
    monkeypatch.setattr(settings, 'sicredi_posto', '13')
    monkeypatch.setattr(settings, 'sicredi_codigo_beneficiario', '11948')
    sicredi.quando('GET', CONSULTA, Resp(200, {}))
    sicredi_client.request('GET', CONSULTA)
    assert sicredi.tokens[0]['url'] == 'https://api-parceiro.sicredi.com.br/auth/openapi/token'
    assert sicredi.tokens[0]['data']['username'] == '119482602'
    assert sicredi.tokens[0]['data']['password'] == 'CODIGO-DE-ACESSO'
    assert sicredi.chamadas[0][2]['headers']['cooperativa'] == '2602'


def test_sem_api_key_a_integracao_fica_desligada(monkeypatch):
    monkeypatch.setattr(settings, 'sicredi_api_key', '')
    assert sicredi_client.habilitado() is False
    with pytest.raises(sicredi_client.SicrediError, match='SICREDI_API_KEY'):
        sicredi_client.credenciais()


def test_log_mascara_documento_nome_e_senha(db, sicredi):
    sicredi.quando('POST', CONSULTA, Resp(201, {'nossoNumero': '1'}))
    sicredi_client.request('POST', CONSULTA, json_body={
        'pagador': {'documento': '12345678901', 'nome': 'JOAO DA SILVA', 'endereco': 'RUA X 10'},
    })
    logs = db.query(SicrediApiLog).order_by(SicrediApiLog.id).all()
    token, cadastro = logs[0], logs[1]
    assert token.request_payload['password'] != 'teste123'
    pagador = cadastro.request_payload['pagador']
    assert pagador['documento'] == '*********01'
    assert pagador['nome'] == 'JOAO ***'
    assert pagador['endereco'] == '***'
