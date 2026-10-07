"""Leitura de NFS-e pela chave da integração, sem emissão ou consulta externa."""
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import event, inspect

from app.core.config import settings
from app.models.billing import Billing
from app.models.enums import BillingStatus
from app.models.nfse_nota import NfseNota
from tests.test_integrations_api import api_key, cobranca  # noqa: F401 — fixtures
from tests.test_nfse_danfse import XML_JOINVILLE, XML_NACIONAL


@pytest.fixture()
def nota_emitida(db, cobranca):
    nota = NfseNota(
        billing_id=cobranca.id, status='emitida', numero_nfse='17', serie_nfse='40000',
        codigo_verificacao='VERIFICACAO-TESTE', chave_acesso='CHAVE-NFSE-TESTE',
        link_visualizacao='https://consulta.example.invalid/nota/17',
        data_emissao=datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc),
        competencia=date(2026, 10, 1), ambiente='producao', provedor='nacional',
        xml_retorno=XML_NACIONAL, xml_envio='<RPS>NAO-EXPORTAR</RPS>',
        tentativa_id='INTERNO-NAO-EXPORTAR',
    )
    db.add(nota)
    db.commit()
    return nota


def _get(http, api_key, billing_id, suffix=''):
    return http.get(
        f'/api/v1/integrations/cobrancas/{billing_id}/nfse{suffix}',
        headers={'X-API-Key': api_key},
    )


def test_listagem_detalhe_e_nota_retornam_mesmo_objeto(http_unauth, api_key, nota_emitida, monkeypatch):
    monkeypatch.setattr(settings, 'backend_public_url', 'https://api.example.invalid/')
    headers = {'X-API-Key': api_key}
    lista = http_unauth.get('/api/v1/integrations/cobrancas', headers=headers)
    assert lista.status_code == 200
    dados = lista.json()['cobrancas'][0]['nfse']
    assert dados['nota_id'] == nota_emitida.id
    assert dados['billing_id'] == nota_emitida.billing_id
    assert dados['numero_nfse'] == '17'
    assert dados['serie_nfse'] == '40000'
    assert dados['codigo_verificacao'] == 'VERIFICACAO-TESTE'
    assert dados['chave_acesso'] == 'CHAVE-NFSE-TESTE'
    assert dados['data_emissao'].startswith('2026-10-07T12:00:00')
    assert dados['competencia'] == '2026-10-01'
    assert dados['ambiente'] == 'producao'
    assert dados['pdf_disponivel'] is dados['xml_disponivel'] is True
    assert dados['motivo_indisponibilidade'] is None
    assert dados['pdf_url'].startswith(f'https://api.example.invalid/api/v1/public/nfse/{nota_emitida.billing_id}/')
    assert dados['pdf_api_url'] == f'https://api.example.invalid/api/v1/integrations/cobrancas/{nota_emitida.billing_id}/nfse/pdf'
    assert dados['xml_url'] == f'https://api.example.invalid/api/v1/integrations/cobrancas/{nota_emitida.billing_id}/nfse/xml'
    detalhe = http_unauth.get(
        f'/api/v1/integrations/cobrancas/{nota_emitida.billing_id}', headers=headers,
    )
    assert detalhe.status_code == 200
    assert detalhe.json()['nfse'] == dados
    assert _get(http_unauth, api_key, nota_emitida.billing_id).json() == dados
    assert 'xml_retorno' not in dados and 'xml_envio' not in dados and 'tentativa_id' not in dados


def test_sem_nota_retorna_null_na_cobranca(http_unauth, api_key, cobranca):
    response = http_unauth.get('/api/v1/integrations/cobrancas', headers={'X-API-Key': api_key})
    assert response.status_code == 200
    assert response.json()['cobrancas'][0]['nfse'] is None


@pytest.mark.parametrize('suffix', ['', '/pdf', '/xml'])
def test_nota_inexistente_retorna_404(http_unauth, api_key, cobranca, suffix):
    assert _get(http_unauth, api_key, cobranca.id, suffix).status_code == 404
    assert _get(http_unauth, api_key, 999999, suffix).status_code == 404


@pytest.mark.parametrize('suffix', ['', '/pdf', '/xml'])
@pytest.mark.parametrize('chave', [None, 'chave-incorreta'])
def test_rotas_fiscais_exigem_api_key(http_unauth, api_key, nota_emitida, suffix, chave):
    response = http_unauth.get(
        f'/api/v1/integrations/cobrancas/{nota_emitida.billing_id}/nfse{suffix}',
        headers={'X-API-Key': chave} if chave else {},
    )
    assert response.status_code == 401


def test_login_painel_nao_substitui_chave_integracao(http, api_key, nota_emitida):
    response = http.get(f'/api/v1/integrations/cobrancas/{nota_emitida.billing_id}/nfse/pdf')
    assert response.status_code == 401


def test_chave_nao_configurada_retorna_503(http_unauth, nota_emitida, monkeypatch):
    monkeypatch.setattr(settings, 'integration_api_key', '')
    assert _get(http_unauth, 'qualquer-chave', nota_emitida.billing_id).status_code == 503


@pytest.mark.parametrize('status', ['pending', 'processing', 'erro', 'desconhecido', 'cancelada', 'substituida'])
def test_nota_nao_emitida_nao_distribui_documentos(http_unauth, api_key, db, nota_emitida, status):
    nota_emitida.status = status
    db.commit()
    response = _get(http_unauth, api_key, nota_emitida.billing_id)
    assert response.status_code == 200
    dados = response.json()
    assert dados['status'] == status
    assert dados['pdf_disponivel'] is dados['xml_disponivel'] is False
    assert dados['motivo_indisponibilidade'] == 'nfse_nao_emitida'
    assert dados['pdf_url'] is dados['pdf_api_url'] is dados['xml_url'] is None
    for suffix in ('/pdf', '/xml'):
        bloqueio = _get(http_unauth, api_key, nota_emitida.billing_id, suffix)
        assert bloqueio.status_code == 409
        assert bloqueio.json()['detail']['code'] == 'nfse_nao_emitida'


@pytest.mark.parametrize('xml', [None, '', '   ', '\t\r\n '])
def test_emitida_sem_xml_informa_indisponibilidade(http_unauth, api_key, db, nota_emitida, xml):
    nota_emitida.xml_retorno = xml
    db.commit()
    dados = _get(http_unauth, api_key, nota_emitida.billing_id).json()
    assert dados['status'] == 'emitida'
    assert dados['motivo_indisponibilidade'] == 'xml_indisponivel'
    assert dados['pdf_disponivel'] is dados['xml_disponivel'] is False
    assert dados['pdf_url'] is dados['pdf_api_url'] is dados['xml_url'] is None
    lista = http_unauth.get('/api/v1/integrations/cobrancas', headers={'X-API-Key': api_key}).json()
    assert lista['cobrancas'][0]['nfse'] == dados
    for suffix in ('/pdf', '/xml'):
        assert _get(http_unauth, api_key, nota_emitida.billing_id, suffix).status_code == 409


def test_xml_preserva_documento_fiscal_e_acentos(http_unauth, api_key, db, nota_emitida):
    xml = XML_NACIONAL.replace('MONITORAMENTO VEICULAR', 'MONITORAÇÃO VEICULAR')
    nota_emitida.xml_retorno = xml
    db.commit()
    response = _get(http_unauth, api_key, nota_emitida.billing_id, '/xml')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/xml; charset=utf-8'
    assert response.content == xml.encode('utf-8')
    assert response.headers['content-disposition'].endswith(f'nfse_{nota_emitida.billing_id:06d}.xml"')
    assert b'NAO-EXPORTAR' not in response.content


@pytest.mark.parametrize('xml,provedor', [(XML_NACIONAL, 'nacional'), (XML_JOINVILLE, 'joinville')])
def test_pdf_gerado_do_xml_nacional_e_municipal(http_unauth, api_key, db, nota_emitida, monkeypatch, xml, provedor):
    from app.services import nfse_nacional

    nota_emitida.xml_retorno = xml
    nota_emitida.provedor = provedor
    db.commit()
    monkeypatch.setattr(nfse_nacional, 'baixar_danfse', lambda *a, **k: pytest.fail('Não consultar governo'))
    response = _get(http_unauth, api_key, nota_emitida.billing_id, '/pdf')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/pdf'
    assert response.content.startswith(b'%PDF') and len(response.content) > 1000
    assert response.headers['content-disposition'].endswith(f'nfse_{nota_emitida.billing_id:06d}.pdf"')


def test_xml_invalido_nao_gera_pdf(http_unauth, api_key, db, nota_emitida):
    nota_emitida.xml_retorno = '<NFSe-incompleta'
    db.commit()
    assert _get(http_unauth, api_key, nota_emitida.billing_id, '/pdf').status_code == 422


@pytest.mark.parametrize('status', [BillingStatus.PAID, BillingStatus.CANCELED])
def test_nota_emitida_independe_da_disponibilidade_do_boleto(http_unauth, api_key, db, cobranca, nota_emitida, status):
    cobranca.status = status
    db.commit()
    response = http_unauth.get(
        '/api/v1/integrations/cobrancas?status=todos', headers={'X-API-Key': api_key},
    )
    assert response.status_code == 200
    dados = response.json()['cobrancas'][0]
    assert dados['boleto_disponivel'] is False
    assert dados['nfse']['pdf_disponivel'] is True
    assert _get(http_unauth, api_key, cobranca.id, '/xml').status_code == 200


@pytest.mark.parametrize('removido', ['cobranca', 'origem', 'pagador'])
@pytest.mark.parametrize('suffix', ['', '/pdf', '/xml'])
def test_acesso_a_nota_respeita_remocao_da_cobranca_e_clientes(
    http_unauth, api_key, db, cobranca, cliente, outro_cliente, nota_emitida, removido, suffix,
):
    if removido == 'cobranca':
        cobranca.is_deleted = True
    elif removido == 'origem':
        cliente.is_deleted = True
    else:
        cobranca.payer_client_id = outro_cliente.id
        outro_cliente.is_deleted = True
    db.commit()
    assert _get(http_unauth, api_key, nota_emitida.billing_id, suffix).status_code == 404


def test_lista_nao_carrega_xmls_nem_faz_uma_consulta_por_nota(http_unauth, api_key, db, cliente):
    ids = []
    for _ in range(10):
        billing = Billing(client_id=cliente.id, amount=100, due_date=date(2026, 10, 10),
                          status=BillingStatus.PENDING, billing_type='avulsa')
        db.add(billing)
        db.flush()
        db.add(NfseNota(billing_id=billing.id, status='emitida', numero_nfse='17',
                        xml_retorno=XML_NACIONAL, xml_envio='NAO-CARREGAR'))
        ids.append(billing.id)
    db.commit()
    db.expunge_all()
    consultas = []
    notas = []

    def registrar(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            consultas.append(statement)

    def registrar_nota(_session, obj):
        if isinstance(obj, NfseNota):
            notas.append(obj)

    event.listen(db.bind, 'before_cursor_execute', registrar)
    event.listen(db, 'loaded_as_persistent', registrar_nota)
    try:
        response = http_unauth.get('/api/v1/integrations/cobrancas', headers={'X-API-Key': api_key})
    finally:
        event.remove(db.bind, 'before_cursor_execute', registrar)
        event.remove(db, 'loaded_as_persistent', registrar_nota)
    assert response.status_code == 200
    assert len(response.json()['cobrancas']) == 10
    assert len(consultas) == 2  # COUNT + página, independentemente das dez notas
    assert len(notas) == 10
    assert all('xml_retorno' in inspect(nota).unloaded and 'xml_envio' in inspect(nota).unloaded for nota in notas)


def test_leitura_fiscal_nao_emite_nem_altera_nota(http_unauth, api_key, db, nota_emitida, monkeypatch):
    from app.services import nfse_joinville, nfse_nacional

    for provider in (nfse_joinville, nfse_nacional):
        monkeypatch.setattr(provider, 'emitir_nfse', lambda *a, **k: pytest.fail('Leitura não pode emitir'))
        monkeypatch.setattr(provider, 'consultar', lambda *a, **k: pytest.fail('Leitura não pode consultar provedor'))
    antes = (nota_emitida.status, nota_emitida.tentativa_id, nota_emitida.xml_envio, nota_emitida.xml_retorno)
    for suffix in ('', '/pdf', '/xml'):
        assert _get(http_unauth, api_key, nota_emitida.billing_id, suffix).status_code == 200
    db.refresh(nota_emitida)
    assert (nota_emitida.status, nota_emitida.tentativa_id, nota_emitida.xml_envio, nota_emitida.xml_retorno) == antes
