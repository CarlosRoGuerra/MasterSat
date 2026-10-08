"""Link de PDF da NFS-e abre no navegador e protege o documento por token."""
from datetime import date
from urllib.parse import urlsplit

import pytest

from app.api.v1.endpoints.boletos import _public_token as boleto_token
from app.core.config import settings
from app.models.billing import Billing
from app.models.enums import BillingStatus
from app.models.nfse_nota import NfseNota
from tests.test_integrations_api import api_key, cobranca  # noqa: F401 — fixtures
from tests.test_integrations_nfse_api import nota_emitida  # noqa: F401 — fixture
from tests.test_nfse_danfse import XML_NACIONAL


def _link(http, api_key, nota):
    resposta = http.get(
        f'/api/v1/integrations/cobrancas/{nota.billing_id}/nfse',
        headers={'X-API-Key': api_key},
    )
    assert resposta.status_code == 200
    return urlsplit(resposta.json()['pdf_url']).path


def test_pdf_url_da_api_abre_sem_chave_ou_login(http_unauth, api_key, nota_emitida, monkeypatch):
    monkeypatch.setattr(settings, 'backend_public_url', 'https://api.example.invalid/')
    path = _link(http_unauth, api_key, nota_emitida)
    response = http_unauth.get(path)
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/pdf'
    assert response.content.startswith(b'%PDF') and len(response.content) > 1000
    assert response.headers['content-disposition'] == f'inline; filename="nfse_{nota_emitida.billing_id:06d}.pdf"'
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['x-robots-tag'] == 'noindex, noarchive'
    assert api_key not in path and settings.secret_key not in path


def test_pdf_api_url_mantem_download_autenticado(http_unauth, api_key, nota_emitida):
    response = http_unauth.get(
        f'/api/v1/integrations/cobrancas/{nota_emitida.billing_id}/nfse',
        headers={'X-API-Key': api_key},
    )
    api_path = urlsplit(response.json()['pdf_api_url']).path
    assert http_unauth.get(api_path).status_code == 401
    arquivo = http_unauth.get(api_path, headers={'X-API-Key': api_key})
    assert arquivo.status_code == 200 and arquivo.content.startswith(b'%PDF')


@pytest.mark.parametrize('status', [BillingStatus.PAID, BillingStatus.CANCELED])
def test_link_continua_valido_para_nota_emitida_de_cobranca_paga_ou_cancelada(
    http_unauth, api_key, db, cobranca, nota_emitida, status,
):
    path = _link(http_unauth, api_key, nota_emitida)
    cobranca.status = status
    db.commit()
    assert http_unauth.get(path).status_code == 200


@pytest.mark.parametrize('token', ['0' * 64, 'invalido', 'çãó'])
def test_token_invalido_nao_entrega_nota(http_unauth, nota_emitida, token):
    response = http_unauth.get(f'/api/v1/public/nfse/{nota_emitida.billing_id}/{token}')
    assert response.status_code == 404
    assert response.json() == {'detail': 'NFS-e não disponível'}


def test_token_de_boleto_nao_abre_nfse(http_unauth, nota_emitida):
    response = http_unauth.get(
        f'/api/v1/public/nfse/{nota_emitida.billing_id}/{boleto_token(nota_emitida.billing_id)}',
    )
    assert response.status_code == 404


def test_token_de_outra_cobranca_nao_abre_nota(http_unauth, api_key, db, cliente, nota_emitida):
    path = _link(http_unauth, api_key, nota_emitida)
    outra = Billing(client_id=cliente.id, amount=100, due_date=date(2026, 10, 10),
                    status=BillingStatus.PENDING, billing_type='avulsa')
    db.add(outra)
    db.flush()
    nota = NfseNota(billing_id=outra.id, status='emitida', xml_retorno=XML_NACIONAL,
                    chave_acesso=nota_emitida.chave_acesso, numero_nfse=nota_emitida.numero_nfse,
                    serie_nfse=nota_emitida.serie_nfse)
    db.add(nota)
    db.commit()
    token = path.rsplit('/', 1)[1]
    assert http_unauth.get(f'/api/v1/public/nfse/{outra.id}/{token}').status_code == 404
    assert http_unauth.get(_link(http_unauth, api_key, nota)).status_code == 200


@pytest.mark.parametrize('campo', ['chave_acesso', 'numero_nfse', 'serie_nfse'])
def test_alterar_identidade_fiscal_invalida_link_anterior(http_unauth, api_key, db, nota_emitida, campo):
    antigo = _link(http_unauth, api_key, nota_emitida)
    setattr(nota_emitida, campo, 'NOVO-VALOR')
    db.commit()
    assert http_unauth.get(antigo).status_code == 404
    novo = _link(http_unauth, api_key, nota_emitida)
    assert novo != antigo
    assert http_unauth.get(novo).status_code == 200


def test_substituir_registro_da_nota_invalida_link_antigo(http_unauth, api_key, db, nota_emitida):
    path = _link(http_unauth, api_key, nota_emitida)
    campos = dict(
        id=nota_emitida.id + 1, billing_id=nota_emitida.billing_id, status='emitida',
        xml_retorno=nota_emitida.xml_retorno, chave_acesso=nota_emitida.chave_acesso,
        numero_nfse=nota_emitida.numero_nfse, serie_nfse=nota_emitida.serie_nfse,
    )
    db.delete(nota_emitida)
    db.commit()
    nova = NfseNota(**campos)
    db.add(nova)
    db.commit()
    assert http_unauth.get(path).status_code == 404
    assert http_unauth.get(_link(http_unauth, api_key, nova)).status_code == 200


@pytest.mark.parametrize('status', ['pending', 'processing', 'erro', 'desconhecido', 'cancelada', 'substituida'])
def test_link_enviado_nao_entrega_nota_que_deixou_de_ser_emitida(
    http_unauth, api_key, db, nota_emitida, status,
):
    path = _link(http_unauth, api_key, nota_emitida)
    nota_emitida.status = status
    db.commit()
    assert http_unauth.get(path).status_code == 404


@pytest.mark.parametrize('xml', [None, '', '   ', '\t\r\n '])
def test_link_enviado_sem_xml_nao_entrega_documento(http_unauth, api_key, db, nota_emitida, xml):
    path = _link(http_unauth, api_key, nota_emitida)
    nota_emitida.xml_retorno = xml
    db.commit()
    assert http_unauth.get(path).status_code == 404


@pytest.mark.parametrize('removido', ['cobranca', 'origem', 'pagador', 'nota'])
def test_link_enviado_respeita_remocoes(
    http_unauth, api_key, db, cliente, outro_cliente, cobranca, nota_emitida, removido,
):
    path = _link(http_unauth, api_key, nota_emitida)
    if removido == 'cobranca':
        cobranca.is_deleted = True
    elif removido == 'origem':
        cliente.is_deleted = True
    elif removido == 'pagador':
        cobranca.payer_client_id = outro_cliente.id
        outro_cliente.is_deleted = True
    else:
        db.delete(nota_emitida)
    db.commit()
    assert http_unauth.get(path).status_code == 404


def test_chave_integracao_nao_e_exigida_pelo_link_enviado(http_unauth, api_key, nota_emitida, monkeypatch):
    path = _link(http_unauth, api_key, nota_emitida)
    monkeypatch.setattr(settings, 'integration_api_key', '')
    assert http_unauth.get(path).status_code == 200


def test_trocar_chave_de_assinatura_revoga_link_enviado(http_unauth, api_key, nota_emitida, monkeypatch):
    antigo = _link(http_unauth, api_key, nota_emitida)
    monkeypatch.setattr(settings, 'secret_key', 'new-test-secret-key-01234567890123456789')
    assert http_unauth.get(antigo).status_code == 404
    assert http_unauth.get(_link(http_unauth, api_key, nota_emitida)).status_code == 200


def test_nota_inexistente_nao_expoe_erro_diferente(http_unauth, cobranca):
    response = http_unauth.get(f'/api/v1/public/nfse/{cobranca.id}/' + '0' * 64)
    assert response.status_code == 404
    assert response.json() == {'detail': 'NFS-e não disponível'}


def test_xml_invalido_retorna_erro_controlado_no_link(http_unauth, api_key, db, nota_emitida):
    path = _link(http_unauth, api_key, nota_emitida)
    nota_emitida.xml_retorno = '<NFSe-incompleta'
    db.commit()
    assert http_unauth.get(path).status_code == 422
