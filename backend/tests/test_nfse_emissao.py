"""Regressões de reserva, ambiguidade, payload e estados fiscais."""
from contextlib import nullcontext
from datetime import date, timedelta
import json

import pytest
import requests
from lxml import etree

from app.core.config import settings
from app.models.nfse_nota import NfseNota
from app.services import nfse_emissao as reservas, nfse_nacional as nacional
from app.services import nfse_joinville as municipal, nfse_lote as lotes, nfse_provider
from tests.test_nfse_lote import _billing, _client, _nota


@pytest.fixture()
def fiscal(db, monkeypatch):
    monkeypatch.setattr(settings, 'nfse_enabled', True)
    monkeypatch.setattr(settings, 'nfse_nac_ambiente', 'producao_restrita')
    monkeypatch.setattr(settings, 'nfse_provedor', 'nacional')
    c = _client(db, 'Tomador fiscal')
    c.address_line, c.address_number, c.neighborhood = 'Rua teste', '1', 'Centro'
    c.zip_code, c.city, c.state = '89201100', 'Joinville', 'SC'
    c.city_ibge_code = '4209102'
    db.commit()
    monkeypatch.setattr(nacional, 'assinar_dps', lambda dps: dps)
    return _billing(db, c), c


def resposta(status=201, **body):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    return response


def xml_nota():
    return f'<NFSe xmlns="{nacional.NS_NFSE}"><infNFSe Id="NFS1234"><nNFSe>987</nNFSe></infNFSe></NFSe>'


def test_timeout_apos_aceite_consulta_dps_sem_segundo_post(db, monkeypatch, fiscal):
    b, c = fiscal
    chamadas = []

    def timeout(*args, **kwargs):
        chamadas.append('POST')
        raise requests.Timeout('aceitou mas a resposta se perdeu')

    monkeypatch.setattr(nacional, '_post', timeout)
    with pytest.raises(nacional.NfseApiError, match='desconhecido'):
        nacional.emitir_nfse(db, b, c)
    nota = db.query(NfseNota).one()
    assert (nota.status, nota.erro_tipo) == ('desconhecido', 'desconhecido')
    assert nota.xml_envio and nota.envio_iniciado_em and nota.dps_id
    assert nacional.emitir_nfse(db, b, c).status == 'desconhecido'
    monkeypatch.setattr(nacional, '_par_pem_mtls', lambda: nullcontext(('cert', 'key')))

    def consulta(url, **kwargs):
        chamadas.append(url)
        return resposta(200, chaveAcesso='1234')

    monkeypatch.setattr(nacional.requests, 'get', consulta)
    monkeypatch.setattr(nacional, 'consultar_por_chave', lambda *args, **kwargs: xml_nota())
    monkeypatch.setattr(settings, 'nfse_provedor', 'joinville')
    monkeypatch.setattr(settings, 'nfse_nac_ambiente', 'producao')
    nota = nfse_provider.consultar_nfse(db, nota)
    assert nota.status == 'emitida' and nota.numero_nfse == '987'
    assert chamadas[0] == 'POST' and len(chamadas) == 2
    assert 'producaorestrita' in chamadas[1] and f'/dps/{nota.dps_id}' in chamadas[1]


@pytest.mark.parametrize('http,body,tipo', [
    (503, {'erros': [{'Descricao': 'indisponível'}]}, 'desconhecido'),
    (409, {'erros': [{'Descricao': 'duplicidade'}]}, 'desconhecido'),
    (400, {'erros': [{'Descricao': 'cadastro inválido'}]}, 'rejeicao'),
    (400, {'gateway': 'bad request'}, 'desconhecido'),
    (400, {'erros': 'proxy'}, 'desconhecido'),
    (400, {'erros': [{'Descricao': 'DPS já emitida'}]}, 'desconhecido'),
    (201, {}, 'desconhecido'),
])
def test_classificacao_http_conservadora(db, monkeypatch, fiscal, http, body, tipo):
    b, c = fiscal
    monkeypatch.setattr(nacional, '_post', lambda *args, **kwargs: resposta(http, **body))
    with pytest.raises(nacional.NfseApiError):
        nacional.emitir_nfse(db, b, c)
    nota = db.query(NfseNota).one()
    assert nota.erro_tipo == tipo
    assert nota.status == ('erro' if tipo == 'rejeicao' else 'desconhecido')


def test_erro_local_antes_do_envio_e_retentativa_preservam_historico(db, monkeypatch, fiscal):
    b, c = fiscal

    def falha(dps):
        raise nacional.NfseError('certificado indisponível')

    monkeypatch.setattr(nacional, 'assinar_dps', falha)
    with pytest.raises(nacional.NfseError):
        nacional.emitir_nfse(db, b, c)
    nota = db.query(NfseNota).one()
    primeiro = nota.tentativa_id
    assert nota.erro_tipo == 'local' and nota.envio_iniciado_em is None
    monkeypatch.setattr(nacional, 'assinar_dps', lambda dps: dps)
    monkeypatch.setattr(nacional, '_post', lambda *args, **kwargs: resposta(nfseXmlGZipB64=nacional._compactar(xml_nota())))
    nota = nacional.emitir_nfse(db, b, c)
    assert nota.status == 'emitida' and nota.tentativa_numero == 2
    assert nota.tentativas_anteriores[0]['tentativa_id'] == primeiro


def test_payload_lote_historico_e_discriminacao_chegam_ao_xml(db, monkeypatch, fiscal):
    b, c = fiscal
    lote = lotes.criar_lote(db, b.period_label, [b.id], emitir_async=False,
                          competencia=date(2024, 2, 1), discriminacao='Exclusivo — manutenção 2024',
                          codigo_servico='110201')
    monkeypatch.setattr(nacional, '_post', lambda *args, **kwargs: resposta(nfseXmlGZipB64=nacional._compactar(xml_nota())))
    nota = nacional.emitir_nfse(db, b, c, lote_id=lote.id)
    root = etree.fromstring(nota.xml_envio.encode())
    assert root.findtext(f'.//{{{nacional.NS_NFSE}}}dCompet') == '2024-02-01'
    assert root.findtext(f'.//{{{nacional.NS_NFSE}}}xDescServ') == nota.discriminacao == 'Exclusivo — manutenção 2024'
    assert root.findtext(f'.//{{{nacional.NS_NFSE}}}cTribNac') == nota.codigo_servico == '110201'


@pytest.mark.parametrize('estado', ['processing', 'desconhecido', 'pending', 'erro'])
def test_lote_nao_conclui_com_desfecho_pendente_ou_erro_legado(db, estado):
    b = _billing(db, _client(db, 'Cliente'))
    lote = lotes.criar_lote(db, b.period_label, [b.id], emitir_async=False)
    nota = db.query(NfseNota).one()
    nota.status = estado
    db.commit()
    lotes._fechar_lote(db, lote.id)
    assert lote.status == 'processando' and lote.concluido_em is None


def test_boot_preserva_fila_ativa_e_expiracao_nao_permite_reenvio(db, fiscal):
    b, c = fiscal
    lote = lotes.criar_lote(db, b.period_label, [b.id], emitir_async=False)
    nota = db.query(NfseNota).one()
    assert lotes.recuperar_notas_orfas(db) == 0
    nota.lease_expires_at = reservas.agora(db) - timedelta(seconds=1)
    db.commit()
    # Mesmo antes do recuperador executar, emissão de fila expirada é bloqueada.
    assert nacional.emitir_nfse(db, b, c, lote_id=lote.id).status == 'desconhecido'
    assert nota.envio_iniciado_em is None


def test_elegibilidade_obsoleta_nao_regride_emitida(db, monkeypatch):
    b = _billing(db, _client(db, 'Cliente'))
    nota = _nota(db, b, status='emitida')
    nota.numero_nfse, nota.xml_retorno = '999', '<NFSe/>'
    db.commit()
    monkeypatch.setattr(lotes, 'listar_elegiveis', lambda *args: {'itens': [{'billing_id': b.id}]})
    with pytest.raises(lotes.LoteError):
        lotes.criar_lote(db, b.period_label, [b.id], emitir_async=False)
    db.refresh(nota)
    assert (nota.status, nota.numero_nfse, nota.xml_retorno, nota.lote_id) == ('emitida', '999', '<NFSe/>', None)


def test_excecao_tardia_lote_nao_apaga_autorizacao(db):
    b = _billing(db, _client(db, 'Cliente'))
    lote = lotes.criar_lote(db, b.period_label, [b.id], emitir_async=False)
    nota = db.query(NfseNota).one()

    def autorizado(db, *args, **kwargs):
        nota.status = 'emitida'
        nota.numero_nfse = '888'
        db.commit()
        raise RuntimeError('falha depois do commit fiscal')

    lotes._emitir_uma(db, nota, autorizado)
    db.refresh(nota)
    assert nota.status == 'emitida' and nota.numero_nfse == '888'


def test_rejeicao_reentrada_lote_preserva_xml_token_e_identidade(db, monkeypatch, fiscal):
    b, c = fiscal
    monkeypatch.setattr(nacional, '_post', lambda *args, **kwargs: resposta(400, erros=[{'Descricao': 'inválido'}]))
    with pytest.raises(nacional.NfseApiError):
        nacional.emitir_nfse(db, b, c)
    nota = db.query(NfseNota).one()
    token, xml = nota.tentativa_id, nota.xml_envio
    lote = lotes.criar_lote(db, b.period_label, [b.id], emitir_async=False)
    db.refresh(nota)
    assert nota.tentativas_anteriores[0]['tentativa_id'] == token
    assert nota.tentativas_anteriores[0]['xml_envio'] == xml
    monkeypatch.setattr(settings, 'nfse_nac_serie', '40001')
    with pytest.raises(nacional.NfseError, match='original'):
        nacional.emitir_nfse(db, b, c, lote_id=lote.id)
    assert nota.serie_rps == '40000'


def test_joinville_processing_nao_conclui_lote_e_consulta_por_rps(db, monkeypatch, fiscal):
    b, c = fiscal
    lote = lotes.criar_lote(db, b.period_label, [b.id], emitir_async=False)
    nota, token = reservas.reservar(db, b.id, lote_id=lote.id, provedor='joinville', ambiente='homologacao',
                                  prestador_cnpj='00000000000191', prestador_im='123', numero_rps=str(b.id), serie_rps='1')
    reservas.atualizar(db, nota.id, token, status='desconhecido')
    calls = []

    def consulta(service, soap, action, **kwargs):
        calls.append(action)
        assert 'IdentificacaoRps' in soap
        return '<Envelope><return>&lt;Resposta/&gt;</return></Envelope>'

    monkeypatch.setattr(municipal, '_post', consulta)
    assert municipal.consultar(db, nota).status == 'desconhecido'
    assert calls == ['ConsultarNfseRps']
    nota.protocolo, nota.status = 'PROTOCOLO', 'processing'
    db.commit()
    lotes._fechar_lote(db, lote.id)
    assert lote.status == 'processando'


def test_joinville_recusa_historico_sem_inventar_data_emissao(db, monkeypatch, fiscal):
    b, c = fiscal
    with pytest.raises(municipal.NfseError, match='histórica'):
        municipal.emitir_nfse(db, b, c, competencia=date(2024, 1, 1))
    nota = db.query(NfseNota).one()
    assert nota.erro_tipo == 'local' and nota.envio_iniciado_em is None


def test_erro_local_em_lote_admite_revisao_individual(db, monkeypatch, fiscal):
    b, c = fiscal
    lote = lotes.criar_lote(db, b.period_label, [b.id], emitir_async=False)
    nota, token = reservas.reservar(db, b.id, lote_id=lote.id)
    reservas.atualizar(db, nota.id, token, status='erro', erro_tipo='local')
    monkeypatch.setattr(nacional, '_post', lambda *args, **kwargs: resposta(nfseXmlGZipB64=nacional._compactar(xml_nota())))
    assert nacional.emitir_nfse(db, b, c).status == 'emitida'
