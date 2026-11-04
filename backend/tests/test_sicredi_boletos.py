"""Boletos Sicredi: emissão, desfecho, política entre bancos, conciliação, baixa e endpoints."""
from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import requests

from app.api.v1.endpoints.boletos import _public_token
from app.core.config import settings
from app.core.timezone import hoje
from app.models.billing import Billing
from app.models.billing_change_log import BillingChangeLog
from app.models.enums import BillingStatus
from app.models.sicredi_boleto import SicrediBoleto
from app.services import sicredi_boletos, sicredi_client, titulo_bancario
from tests.fase03_apoio import registrar_titulo, reserva
from tests.sicredi_apoio import Resp, resposta_cadastro, resposta_consulta, sicredi  # noqa: F401

BOLETOS = '/cobranca/boleto/v1/boletos'
PDF = f'{BOLETOS}/pdf'
LIQUIDADOS = f'{BOLETOS}/liquidados/dia'
PDF_FALSO = b'%PDF-1.4 boleto sicredi'


def _texto_pdf(conteudo: bytes) -> str:
    from pypdf import PdfReader
    return '\n'.join(page.extract_text() for page in PdfReader(io.BytesIO(conteudo)).pages)


def _eco_cadastro(params=None, json=None, headers=None):
    return resposta_cadastro(json['nossoNumero'])


def _emitir(db, billing) -> SicrediBoleto:
    return sicredi_boletos.gerar_boleto(db, billing.id)


# ---------------------------------------------------------------------------
# Texto e payload (regras da pasta sicred)
# ---------------------------------------------------------------------------

def test_texto_so_com_caracteres_validos_do_sicredi():
    assert sicredi_boletos.texto_sicredi("João d'Ávila ~ ^ `Ção`") == 'JOAO D AVILA CAO'
    # NFKD decompõe o "º" em "o": "nº" vira "NO".
    assert sicredi_boletos.texto_sicredi('Rua "Sete" & Cia, nº 10') == 'RUA SETE & CIA, NO 10'
    assert len(sicredi_boletos.texto_sicredi('X' * 90)) == 40


def test_emite_boleto_normal_sem_encargos(db, billing_pendente, sicredi):
    sicredi.quando('POST', BOLETOS, _eco_cadastro)
    boleto = _emitir(db, billing_pendente)

    payload = sicredi.chamadas_de('POST', BOLETOS)[0]['json']
    assert payload['tipoCobranca'] == 'NORMAL'
    assert payload['codigoBeneficiario'] == '12345'
    assert payload['seuNumero'] == str(billing_pendente.id)
    assert payload['valor'] == 99.9
    assert payload['especieDocumento'] == 'DUPLICATA_SERVICO_INDICACAO'
    assert payload['nossoNumero'] == boleto.nosso_numero
    assert len(boleto.nosso_numero) == 9 and boleto.nosso_numero[2] == '2'
    assert payload['pagador'] == {'tipoPessoa': 'PESSOA_FISICA', 'documento': '12345678901',
                                  'nome': 'JOAO SILVA', 'telefone': '11999990000',
                                  'email': 'joao@test.local'}
    for campo in ('juros', 'multa', 'tipoJuros', 'tipoMulta', 'dataInicioJuros', 'dataInicioMulta',
                  'diasProtestoAuto', 'diasNegativacaoAuto', 'tipoDesconto'):
        assert campo not in payload

    assert boleto.status == 'REGISTRADO' and boleto.linha_digitavel
    titulo = titulo_bancario.titulos_bancarios(db, [billing_pendente.id])[billing_pendente.id]
    assert (titulo.estado, titulo.canal) == (titulo_bancario.REGISTRADO, 'sicredi_api')


def test_reemitir_titulo_registrado_e_idempotente(db, billing_pendente, sicredi):
    sicredi.quando('POST', BOLETOS, _eco_cadastro)
    primeiro = _emitir(db, billing_pendente)
    segundo = _emitir(db, billing_pendente)
    assert segundo.id == primeiro.id
    assert len(sicredi.chamadas_de('POST', BOLETOS)) == 1


def test_hibrido_nao_envia_data_de_inicio_de_juros_nem_multa(db, billing_pendente, sicredi, monkeypatch):
    monkeypatch.setattr(settings, 'sicredi_boleto_hibrido', True)
    monkeypatch.setattr(settings, 'sicredi_validade_apos_vencimento', 30)
    sicredi.quando('POST', BOLETOS, lambda **kw: resposta_cadastro(kw['json']['nossoNumero'], qr='000201PIX'))
    boleto = _emitir(db, billing_pendente)
    payload = sicredi.chamadas_de('POST', BOLETOS)[0]['json']
    assert payload['tipoCobranca'] == 'HIBRIDO'
    assert payload['validadeAposVencimento'] == 30
    assert 'dataInicioJuros' not in payload and 'dataInicioMulta' not in payload
    assert boleto.qr_code == '000201PIX' and boleto.erro is None


def test_hibrido_registrado_sem_qr_avisa(db, billing_pendente, sicredi, monkeypatch):
    monkeypatch.setattr(settings, 'sicredi_boleto_hibrido', True)
    sicredi.quando('POST', BOLETOS, _eco_cadastro)
    boleto = _emitir(db, billing_pendente)
    assert boleto.status == 'REGISTRADO'
    assert 'modalidade híbrida' in boleto.erro


def test_vencimento_no_passado_recusado_sem_reservar(db, billing_pendente, sicredi):
    billing_pendente.due_date = hoje() - timedelta(days=1)
    db.commit()
    with pytest.raises(sicredi_boletos.SicrediValidationError, match='já passou'):
        _emitir(db, billing_pendente)
    db.rollback()
    assert db.query(SicrediBoleto).count() == 0
    assert sicredi.chamadas == []


def test_carne_somente_sistema_nao_vai_ao_banco(db, billing_pendente, sicredi):
    billing_pendente.somente_sistema = True
    db.commit()
    with pytest.raises(titulo_bancario.PoliticaBancariaError) as exc:
        _emitir(db, billing_pendente)
    assert exc.value.code == 'cobranca_somente_sistema'


# ---------------------------------------------------------------------------
# Falhas e desfecho
# ---------------------------------------------------------------------------

def test_rejeicao_libera_e_a_nova_tentativa_reaproveita_o_nosso_numero(db, billing_pendente, sicredi):
    sicredi.quando('POST', BOLETOS, Resp(422, {'mensagem': 'CEP do pagador obrigatório'}), _eco_cadastro)
    with pytest.raises(sicredi_client.SicrediApiError):
        _emitir(db, billing_pendente)
    db.rollback()
    boleto = db.query(SicrediBoleto).one()
    assert boleto.status == 'ERRO_REGISTRO' and 'CEP' in boleto.erro
    estado = titulo_bancario.titulos_bancarios(db, [billing_pendente.id])[billing_pendente.id].estado
    assert estado == titulo_bancario.SEM_TITULO

    _emitir(db, billing_pendente)
    numeros = [c['json']['nossoNumero'] for c in sicredi.chamadas_de('POST', BOLETOS)]
    assert numeros[0] == numeros[1]


def test_timeout_vira_desfecho_desconhecido_e_bloqueia_tudo(db, billing_pendente, sicredi):
    sicredi.quando('POST', BOLETOS, requests.exceptions.ReadTimeout('timeout'))
    with pytest.raises(sicredi_client.SicrediDesfechoDesconhecido):
        _emitir(db, billing_pendente)
    db.rollback()
    assert db.query(SicrediBoleto).one().status == 'DESFECHO_DESCONHECIDO'
    for operacao in (titulo_bancario.ALTERAR_VALOR, titulo_bancario.CANCELAR, titulo_bancario.EMITIR_AILOS,
                     titulo_bancario.EMITIR_SICREDI):
        with pytest.raises(titulo_bancario.PoliticaBancariaError) as exc:
            titulo_bancario.exigir(db, operacao, [billing_pendente.id])
        assert exc.value.code == 'boleto_ailos_desfecho_desconhecido'
        assert 'Sicredi' in exc.value.message


def test_desfecho_resolvido_pela_consulta_quando_o_titulo_existe(db, billing_pendente, sicredi):
    sicredi.quando('POST', BOLETOS, requests.exceptions.ReadTimeout('timeout'))
    with pytest.raises(sicredi_client.SicrediDesfechoDesconhecido):
        _emitir(db, billing_pendente)
    db.rollback()
    boleto = db.query(SicrediBoleto).one()
    sicredi.quando('GET', BOLETOS, resposta_consulta(boleto.nosso_numero, str(billing_pendente.id)))
    resultado = sicredi_boletos.resolver_desfecho(db, billing_pendente.id)
    assert resultado['resultado'] == 'registrado'
    assert db.query(SicrediBoleto).one().status == 'REGISTRADO'


def test_desfecho_nao_encontrado_depois_do_prazo_libera_com_historico(db, billing_pendente, sicredi):
    sicredi.quando('POST', BOLETOS, requests.exceptions.ReadTimeout('timeout'))
    with pytest.raises(sicredi_client.SicrediDesfechoDesconhecido):
        _emitir(db, billing_pendente)
    db.rollback()
    boleto = db.query(SicrediBoleto).one()
    sicredi.quando('GET', BOLETOS, Resp(404, {'message': 'Boleto não encontrado'}))
    assert sicredi_boletos.resolver_desfecho(db, billing_pendente.id)['resultado'] == 'aguardar'

    boleto.registro_iniciado_em = datetime.now(timezone.utc) - timedelta(minutes=30)
    db.commit()
    assert sicredi_boletos.resolver_desfecho(db, billing_pendente.id)['resultado'] == 'nao_registrado'
    assert db.query(SicrediBoleto).one().status == 'ERRO_REGISTRO'
    historico = db.query(BillingChangeLog).filter_by(field_name='registro_sicredi').one()
    assert historico.new_value == 'ERRO_REGISTRO'


def _reserva_incerta(db, billing, sicredi):
    sicredi.quando('POST', BOLETOS, requests.exceptions.ReadTimeout('timeout'))
    with pytest.raises(sicredi_client.SicrediDesfechoDesconhecido):
        _emitir(db, billing)
    db.rollback()
    boleto = db.query(SicrediBoleto).filter_by(billing_id=billing.id).one()
    boleto.registro_iniciado_em = datetime.now(timezone.utc) - timedelta(minutes=30)
    db.commit()
    return boleto


@pytest.mark.parametrize('corpo', [None, [], {}, 'HTML sem dados do título'])
def test_resposta_invalida_na_consulta_nao_confirma_ausencia(db, billing_pendente, sicredi, corpo):
    boleto = _reserva_incerta(db, billing_pendente, sicredi)
    sicredi.quando('GET', BOLETOS, Resp(200, corpo))
    resultado = sicredi_boletos.resolver_desfecho(db, billing_pendente.id)
    assert resultado['resultado'] == 'indisponivel'
    db.refresh(boleto)
    assert boleto.status == 'DESFECHO_DESCONHECIDO'
    assert boleto.ultima_consulta_em is not None and boleto.ultima_consulta_erro
    with pytest.raises(titulo_bancario.PoliticaBancariaError):
        titulo_bancario.exigir(db, titulo_bancario.EMITIR_AILOS, [billing_pendente.id])


@pytest.mark.parametrize('resposta_antiga', ['ausente', 'outro_titulo'])
def test_consulta_antiga_nao_desfaz_registro_confirmado_durante_get(db, billing_pendente, sicredi, resposta_antiga):
    boleto = _reserva_incerta(db, billing_pendente, sicredi)
    numero = boleto.nosso_numero

    def consulta_atrasada(**kwargs):
        sicredi_boletos._gravar_registro(db, billing_pendente.id, resposta_cadastro(numero).json())
        if resposta_antiga == 'ausente':
            return Resp(404, {'message': 'Título não encontrado'})
        return resposta_consulta(numero, 'outro-seu-numero')

    sicredi.quando('GET', BOLETOS, consulta_atrasada)
    assert sicredi_boletos.resolver_desfecho(db, billing_pendente.id)['resultado'] == 'registrado'
    db.refresh(boleto)
    assert boleto.status == 'REGISTRADO' and boleto.codigo_barras and boleto.linha_digitavel
    assert db.query(BillingChangeLog).filter_by(field_name='registro_sicredi', new_value='ERRO_REGISTRO').count() == 0
    with pytest.raises(titulo_bancario.PoliticaBancariaError):
        titulo_bancario.exigir(db, titulo_bancario.EMITIR_AILOS, [billing_pendente.id])


def test_resposta_de_reserva_anterior_nao_sobrepoe_nova_geracao(db, billing_pendente, sicredi):
    boleto = _reserva_incerta(db, billing_pendente, sicredi)
    numero_anterior = boleto.nosso_numero

    def consulta_de_reserva_anterior(**kwargs):
        boleto.nosso_numero = '269003806'
        boleto.registro_iniciado_em = datetime.now(timezone.utc)
        boleto.status = 'REGISTRANDO'
        db.commit()
        return resposta_consulta(numero_anterior, str(billing_pendente.id))

    sicredi.quando('GET', BOLETOS, consulta_de_reserva_anterior)
    assert sicredi_boletos.resolver_desfecho(db, billing_pendente.id)['resultado'] == 'sem_pendencia'
    db.refresh(boleto)
    assert boleto.nosso_numero == '269003806' and boleto.status == 'REGISTRANDO'
    assert boleto.codigo_barras is None


def test_ja_cadastrado_recupera_o_titulo_desta_cobranca(db, billing_pendente, sicredi):
    def _consulta(**kw):
        return resposta_consulta(kw['params']['nossoNumero'], str(billing_pendente.id))
    sicredi.quando('POST', BOLETOS, Resp(422, {'mensagem': 'Nosso número já cadastrado para o beneficiário'}))
    sicredi.quando('GET', BOLETOS, _consulta)
    boleto = _emitir(db, billing_pendente)
    assert boleto.status == 'REGISTRADO'


def test_nosso_numero_de_outro_titulo_nunca_e_anexado(db, billing_pendente, sicredi):
    # Sequencial voltou atrás (restauração de backup): o número já é de outro título.
    def _consulta(**kw):
        return resposta_consulta(kw['params']['nossoNumero'], '999999')
    sicredi.quando('POST', BOLETOS, Resp(422, {'mensagem': 'Nosso número já cadastrado'}), _eco_cadastro)
    sicredi.quando('GET', BOLETOS, _consulta)
    with pytest.raises(sicredi_client.SicrediError, match='outro título'):
        _emitir(db, billing_pendente)
    db.rollback()
    boleto = db.query(SicrediBoleto).one()
    assert boleto.status == 'ERRO_REGISTRO' and boleto.linha_digitavel is None

    _emitir(db, billing_pendente)
    numeros = [c['json']['nossoNumero'] for c in sicredi.chamadas_de('POST', BOLETOS)]
    assert numeros[0] != numeros[1]


# ---------------------------------------------------------------------------
# Um título por cobrança entre os dois bancos
# ---------------------------------------------------------------------------

def test_titulo_na_ailos_bloqueia_emissao_no_sicredi(db, billing_pendente, sicredi):
    registrar_titulo(db, billing_pendente)
    with pytest.raises(titulo_bancario.PoliticaBancariaError) as exc:
        _emitir(db, billing_pendente)
    assert exc.value.code == 'boleto_ailos_registrado'
    assert sicredi.chamadas_de('POST', BOLETOS) == []


def test_titulo_no_sicredi_bloqueia_emissao_na_ailos(db, billing_pendente, sicredi):
    sicredi.quando('POST', BOLETOS, _eco_cadastro)
    _emitir(db, billing_pendente)
    with pytest.raises(titulo_bancario.PoliticaBancariaError) as exc:
        titulo_bancario.exigir(db, titulo_bancario.EMITIR_AILOS, [billing_pendente.id])
    assert exc.value.code == 'boleto_ailos_registrado'
    assert 'Sicredi' in exc.value.message


@pytest.mark.parametrize('status', ['REGISTRANDO', 'PROCESSANDO'])
def test_nova_tentativa_sicredi_nao_sobrepoe_reserva_ailos(db, http, billing_pendente, sicredi, status):
    reserva(db, billing_pendente, status)
    res = http.post('/api/v1/sicredi/boletos', json={'billing_id': billing_pendente.id, 'nova_tentativa': True})
    assert res.status_code == 409, res.text
    assert res.json()['detail']['estado_bancario'] == 'em_registro'
    assert db.query(SicrediBoleto).count() == 0
    assert sicredi.chamadas == []


def test_nova_tentativa_nao_reenvia_sicredi_ainda_em_registro(db, http, billing_pendente, sicredi):
    def cadastro_em_andamento(**kwargs):
        res = http.post('/api/v1/sicredi/boletos', json={'billing_id': billing_pendente.id, 'nova_tentativa': True})
        assert res.status_code == 409, res.text
        assert res.json()['detail']['estado_bancario'] == 'em_registro'
        return _eco_cadastro(**kwargs)

    sicredi.quando('POST', BOLETOS, cadastro_em_andamento)
    assert _emitir(db, billing_pendente).status == 'REGISTRADO'
    assert len(sicredi.chamadas_de('POST', BOLETOS)) == 1


def test_retry_ailos_nao_sobrepoe_reserva_sicredi(db, billing_pendente, sicredi):
    boleto = _registrado(db, billing_pendente, sicredi)
    boleto.status = 'REGISTRANDO'
    boleto.linha_digitavel = boleto.codigo_barras = None
    db.commit()
    with pytest.raises(titulo_bancario.PoliticaBancariaError):
        titulo_bancario.exigir(db, titulo_bancario.EMITIR_AILOS, [billing_pendente.id],
                              permitir_em_registro={billing_pendente.id})


def test_cancelar_pede_confirmacao_e_deixa_baixa_pendente(db, http, billing_pendente, sicredi):
    sicredi.quando('POST', BOLETOS, _eco_cadastro)
    _emitir(db, billing_pendente)

    resp = http.post(f'/api/v1/billings/{billing_pendente.id}/cancel', json={'reason': 'teste'})
    assert resp.status_code == 409
    assert resp.json()['detail']['code'] == 'boleto_ailos_registrado'
    assert 'Dar baixa no Sicredi' in resp.json()['detail']['message']

    resp = http.post(f'/api/v1/billings/{billing_pendente.id}/cancel',
                     json={'reason': 'teste', 'confirmar_boleto_ailos': True})
    assert resp.status_code == 200, resp.text
    db.expire_all()
    assert db.get(Billing, billing_pendente.id).status == BillingStatus.CANCELED
    assert db.query(SicrediBoleto).one().baixa_status == 'pendente'


# ---------------------------------------------------------------------------
# Consulta, conciliação e baixa
# ---------------------------------------------------------------------------

def _registrado(db, billing, sicredi) -> SicrediBoleto:
    sicredi.quando('POST', BOLETOS, _eco_cadastro)
    return _emitir(db, billing)


def test_liquidado_quita_a_cobranca_com_a_data_de_brasilia(db, billing_pendente, sicredi):
    boleto = _registrado(db, billing_pendente, sicredi)
    sicredi.quando('GET', BOLETOS, resposta_consulta(
        boleto.nosso_numero, str(billing_pendente.id), 'LIQUIDADO PIX',
        liquidacao={'data': '2026-10-31T01:30:00Z', 'valor': 99.9},
    ))
    resultado = sicredi_boletos.consultar(db, billing_pendente.id)
    assert resultado['quitada'] is True
    db.expire_all()
    billing = db.get(Billing, billing_pendente.id)
    assert billing.status == BillingStatus.PAID and billing.payment_method == 'boleto'
    assert billing.payment_date == date(2026, 10, 30)  # 01:30 UTC = 22:30 em Brasília
    assert db.query(SicrediBoleto).one().baixa_status == 'confirmada'


def test_pagamento_com_valor_diferente_vira_pendencia(db, billing_pendente, sicredi):
    boleto = _registrado(db, billing_pendente, sicredi)
    sicredi.quando('GET', BOLETOS, resposta_consulta(
        boleto.nosso_numero, str(billing_pendente.id), 'LIQUIDADO COMPE',
        liquidacao={'data': '2026-10-30', 'valor': 80.0},
    ))
    resultado = sicredi_boletos.consultar(db, billing_pendente.id)
    assert resultado['pendencia'] == 'pagamento_divergente' and resultado['quitada'] is False
    db.expire_all()
    assert db.get(Billing, billing_pendente.id).status == BillingStatus.PENDING


def test_baixado_no_banco_com_cobranca_aberta_vira_pendencia(db, billing_pendente, sicredi):
    boleto = _registrado(db, billing_pendente, sicredi)
    sicredi.quando('GET', BOLETOS, resposta_consulta(
        boleto.nosso_numero, str(billing_pendente.id), 'BAIXADO POR SOLICITACAO'))
    resultado = sicredi_boletos.consultar(db, billing_pendente.id)
    assert resultado['pendencia'] == 'baixado_com_cobranca_aberta'
    assert db.query(SicrediBoleto).one().baixa_status == 'confirmada'


def test_rejeitado_libera_nova_emissao(db, billing_pendente, sicredi):
    boleto = _registrado(db, billing_pendente, sicredi)
    sicredi.quando('GET', BOLETOS, resposta_consulta(boleto.nosso_numero, str(billing_pendente.id), 'REJEITADO'))
    sicredi_boletos.consultar(db, billing_pendente.id)
    estado = titulo_bancario.titulos_bancarios(db, [billing_pendente.id])[billing_pendente.id].estado
    assert estado == titulo_bancario.SEM_TITULO


def test_baixa_pela_api_fica_solicitada_ate_a_consulta(db, billing_pendente, sicredi):
    boleto = _registrado(db, billing_pendente, sicredi)
    caminho = f'{BOLETOS}/{boleto.nosso_numero}/baixa'
    sicredi.quando('PATCH', caminho, Resp(202, {'transactionId': 'abc-123', 'statusComando': 'MOVIMENTO_ENVIADO'}))
    resultado = sicredi_boletos.solicitar_baixa(db, billing_pendente.id, user_id=1, motivo='cliente pagou em dinheiro')
    assert resultado['baixa_status'] == 'solicitada'
    assert sicredi.chamadas_de('PATCH', caminho)[0]['headers']['codigoBeneficiario'] == '12345'
    boleto = db.query(SicrediBoleto).one()
    assert boleto.baixa_transaction_id == 'abc-123'
    assert db.query(BillingChangeLog).filter_by(field_name='baixa_sicredi').count() == 1

    sicredi.quando('GET', BOLETOS, resposta_consulta(
        boleto.nosso_numero, str(billing_pendente.id), 'BAIXADO POR SOLICITACAO'))
    sicredi_boletos.consultar(db, billing_pendente.id)
    assert db.query(SicrediBoleto).one().baixa_status == 'confirmada'


def test_conciliacao_aplica_os_liquidados_do_dia(db, billing_pendente, sicredi):
    boleto = _registrado(db, billing_pendente, sicredi)
    sicredi.quando('GET', LIQUIDADOS, Resp(200, {
        'items': [{'nossoNumero': boleto.nosso_numero, 'valorLiquidado': 99.9, 'tipoLiquidacao': 'PIX'}],
        'hasNext': False,
    }))
    sicredi.quando('GET', BOLETOS, resposta_consulta(
        boleto.nosso_numero, str(billing_pendente.id), 'LIQUIDADO PIX',
        liquidacao={'data': '2026-10-30', 'valor': 99.9},
    ))
    metricas = sicredi_boletos.conciliar(db, orcamento=10)
    assert metricas['liquidados_no_banco'] == 1 and metricas['quitados'] == 1
    db.expire_all()
    assert db.get(Billing, billing_pendente.id).status == BillingStatus.PAID


# ---------------------------------------------------------------------------
# Endpoints, PDF e link público
# ---------------------------------------------------------------------------

def test_conciliacao_faz_rodizio_dos_incertos_mesmo_com_banco_indisponivel(db, cliente, sicredi):
    momento = datetime.now(timezone.utc) - timedelta(minutes=30)
    for indice in range(51):
        billing = Billing(client_id=cliente.id, amount=Decimal('99.90'), due_date=date(2099, 12, 31),
                          status=BillingStatus.PENDING, billing_type='avulsa', title='Rodízio de consulta')
        db.add(billing)
        db.flush()
        db.add(SicrediBoleto(billing_id=billing.id, ambiente='sandbox', cooperativa='6789', posto='03',
                            codigo_beneficiario='12345', nosso_numero=f'269{indice:05d}0',
                            status='DESFECHO_DESCONHECIDO', registro_iniciado_em=momento))
    db.commit()
    sicredi.quando('GET', BOLETOS, Resp(503, {'message': 'Indisponível'}))
    sicredi.quando('GET', LIQUIDADOS, Resp(200, {'items': [], 'hasNext': False}))
    sicredi_boletos.conciliar(db, orcamento=0, dias=[hoje()])
    assert db.query(SicrediBoleto).filter(SicrediBoleto.ultima_consulta_em.is_not(None)).count() == 50
    sicredi_boletos.conciliar(db, orcamento=0, dias=[hoje()])
    assert db.query(SicrediBoleto).filter(SicrediBoleto.ultima_consulta_em.is_not(None)).count() == 51


def test_operacional_nao_emite(http_op, billing_pendente, sicredi):
    resp = http_op.post('/api/v1/sicredi/boletos', json={'billing_id': billing_pendente.id})
    assert resp.status_code == 403


def test_status_nao_chama_o_banco(http, sicredi):
    resp = http.get('/api/v1/sicredi/status')
    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo['habilitado'] is True and corpo['ambiente'] == 'sandbox'
    assert corpo['codigo_beneficiario'] == '12345'
    assert sicredi.tokens == [] and sicredi.chamadas == []


def test_emite_pela_api_e_pdf_usa_design_mastersat_com_dados_sicredi(db, http, billing_pendente, sicredi):
    sicredi.quando('POST', BOLETOS, _eco_cadastro)
    sicredi.quando('GET', PDF, Resp(201, None, content=PDF_FALSO, headers={'Content-Type': 'application/octet-stream'}))
    resp = http.post('/api/v1/sicredi/boletos', json={'billing_id': billing_pendente.id})
    assert resp.status_code == 200, resp.text
    assert resp.json()['estado'] == 'registrado'

    resp = http.get(f'/api/v1/boletos/{billing_pendente.id}/pdf')
    assert resp.status_code == 200
    texto = _texto_pdf(resp.content)
    assert 'Recibo do pagador' in texto and 'MASTERSAT' in texto
    assert '748-X' in texto and '085-0' not in texto and 'AILOS' not in texto
    assert '99,90' in texto and 'SEM VALIDADE FINANCEIRA' in texto
    assert '74891.12511' in texto
    assert sicredi.chamadas_de('GET', PDF) == []
    pdf_especifico = http.get(f'/api/v1/sicredi/boletos/{billing_pendente.id}/pdf')
    assert pdf_especifico.status_code == 200
    assert _texto_pdf(pdf_especifico.content) == texto

    dados = http.get(f'/api/v1/boletos/{billing_pendente.id}').json()
    assert dados['boleto_registrado'] is True and dados['banco']['codigo'] == '748'


def test_link_publico_entrega_o_pdf_e_some_com_baixa(db, http, billing_pendente, sicredi):
    _registrado(db, billing_pendente, sicredi)
    sicredi.quando('GET', PDF, Resp(201, None, content=PDF_FALSO, headers={'Content-Type': 'application/octet-stream'}))
    url = f'/api/v1/public/boleto/{billing_pendente.id}/{_public_token(billing_pendente.id)}'
    assert '748-X' in _texto_pdf(http.get(url).content)

    db.query(SicrediBoleto).one().baixa_status = 'pendente'
    db.commit()
    assert http.get(url).status_code == 404


def test_impressao_preserva_dados_registrados_e_detalhamento_da_ailos(db, billing_pendente, sicredi, monkeypatch):
    from app.api.v1.endpoints import boletos
    from app.models.client import Client

    boleto = _registrado(db, billing_pendente, sicredi)
    # A renderização usa os valores do registro, mesmo que configurações ou
    # dados da cobrança tenham mudado desde a emissão.
    boleto.ambiente = 'producao'
    boleto.cooperativa = '2602'
    boleto.posto = '13'
    boleto.codigo_beneficiario = '11948'
    boleto.data_vencimento = hoje() + timedelta(days=14)
    boleto.valor_nominal = Decimal('99.90')
    boleto.qr_code = 'PIX-RETORNADO-PELO-BANCO'
    cliente = db.get(Client, billing_pendente.client_id)
    itens_ailos = boletos._itens_detalhados(billing_pendente, db)
    monkeypatch.setattr(settings, 'sicredi_cooperativa', '9999')
    monkeypatch.setattr(settings, 'sicredi_codigo_beneficiario', '99999')
    monkeypatch.setattr(boletos, 'gerar_dados_boleto', lambda **kw: pytest.fail('Não calcular código Ailos para o Sicredi'))
    dados = boletos.dados_boleto_sicredi(billing_pendente, cliente, db, boleto)
    assert dados.itens == itens_ailos
    assert dados.codigo_barras == boleto.codigo_barras
    assert re.sub(r'\D', '', dados.linha_digitavel) == boleto.linha_digitavel
    assert dados.pix_emv == boleto.qr_code
    assert dados.cedente_agencia == '2602.13' and dados.cedente_codigo == '11948'
    assert dados.nosso_numero_display == f'{boleto.nosso_numero[:2]}/{boleto.nosso_numero[2:8]}-{boleto.nosso_numero[8:]}'
    assert dados.numero_documento == boleto.seu_numero
    assert dados.valor == Decimal('99.90') and dados.data_vencimento == boleto.data_vencimento
    assert not any('SEM VALIDADE FINANCEIRA' in instrucao for instrucao in dados.instrucoes)


def test_listagem_mostra_boleto_emitido_e_titulo_do_sicredi(db, http, billing_pendente, billing_vencida, sicredi):
    _registrado(db, billing_pendente, sicredi)
    emitidas = http.get('/api/v1/billings/', params={'boleto_emitido': True}).json()
    assert [b['id'] for b in emitidas] == [billing_pendente.id]
    assert emitidas[0]['boleto_ailos'] is True
    assert emitidas[0]['titulo_bancario']['canal'] == 'sicredi_api'
    sem_boleto = http.get('/api/v1/billings/', params={'boleto_emitido': False}).json()
    assert billing_pendente.id not in [b['id'] for b in sem_boleto]
    assert billing_vencida.id in [b['id'] for b in sem_boleto]


def test_pdf_disponivel_depois_do_cadastro_repete_apenas_download(db, http, billing_pendente, sicredi, monkeypatch):
    esperas = []
    monkeypatch.setattr(sicredi_boletos.time, 'sleep', esperas.append)
    _registrado(db, billing_pendente, sicredi)
    sicredi.quando('GET', PDF, Resp(422, {'message': 'Boleto não localizado.'}), Resp(201, content=PDF_FALSO))
    assert sicredi_boletos.pdf(db, billing_pendente.id) == PDF_FALSO
    assert esperas == [2]
    assert len(sicredi.chamadas_de('POST', BOLETOS)) == 1
    assert len(sicredi.chamadas_de('GET', PDF)) == 2


def test_email_anexa_pdf_sicredi_no_design_mastersat(db, http, billing_pendente, sicredi, monkeypatch):
    from app.models.client import Client
    from app.services import email_smtp

    _registrado(db, billing_pendente, sicredi)
    db.get(Client, billing_pendente.client_id).email = 'pagador@example.com'
    db.commit()
    sicredi.quando('GET', PDF, Resp(201, content=PDF_FALSO))
    enviados = []
    monkeypatch.setattr(email_smtp, 'enviar_email', lambda *a, **kw: enviados.append(kw))
    res = http.post(f'/api/v1/boletos/{billing_pendente.id}/enviar-email')
    assert res.status_code == 200, res.text
    texto = _texto_pdf(enviados[0]['anexo'][1])
    assert 'Recibo do pagador' in texto and '748-X' in texto


def test_integracao_whatsapp_entrega_codigos_e_pdf_do_sicredi(db, http, billing_pendente, sicredi, monkeypatch):
    monkeypatch.setattr(settings, 'integration_api_key', 'integracao-teste')
    monkeypatch.setattr(settings, 'sicredi_boleto_hibrido', True)
    sicredi.quando('POST', BOLETOS, lambda params, json, headers: resposta_cadastro(json['nossoNumero'], qr='PIX-OFICIAL-DO-TESTE'))
    _emitir(db, billing_pendente)
    sicredi.quando('GET', PDF, Resp(201, content=PDF_FALSO))
    headers = {'X-API-Key': 'integracao-teste'}
    detalhe = http.get(f'/api/v1/integrations/cobrancas/{billing_pendente.id}', headers=headers)
    assert detalhe.status_code == 200, detalhe.text
    assert detalhe.json()['banco'] == 'sicredi'
    assert detalhe.json()['boleto_disponivel'] is True
    assert detalhe.json()['pix_copia_cola'] == 'PIX-OFICIAL-DO-TESTE'
    lista = http.get('/api/v1/integrations/cobrancas', headers=headers).json()['cobrancas']
    assert next(b for b in lista if b['id'] == billing_pendente.id)['banco'] == 'sicredi'
    res = http.get(f'/api/v1/integrations/cobrancas/{billing_pendente.id}/pdf', headers=headers)
    assert res.status_code == 200
    assert '748-X' in _texto_pdf(res.content)
    db.query(SicrediBoleto).one().baixa_status = 'pendente'
    db.commit()
    detalhe = http.get(f'/api/v1/integrations/cobrancas/{billing_pendente.id}', headers=headers).json()
    assert detalhe['boleto_disponivel'] is False and detalhe['linha_digitavel'] is None


def test_producao_recusa_codigos_de_outro_titulo_sem_liberar_reemissao(db, billing_pendente, sicredi, monkeypatch):
    monkeypatch.setattr(settings, 'sicredi_ambiente', 'producao')
    monkeypatch.setattr(settings, 'sicredi_producao_confirmada', True)
    monkeypatch.setattr(settings, 'sicredi_cooperativa', '2602')
    monkeypatch.setattr(settings, 'sicredi_posto', '13')
    monkeypatch.setattr(settings, 'sicredi_codigo_beneficiario', '11948')
    monkeypatch.setattr(settings, 'sicredi_codigo_acesso', 'codigo-de-acesso-de-teste')
    sicredi.quando('POST', BOLETOS, _eco_cadastro)
    with pytest.raises(sicredi_client.SicrediDesfechoDesconhecido):
        _emitir(db, billing_pendente)
    assert titulo_bancario.estado_do_sicredi(db.query(SicrediBoleto).one()) == 'desfecho_desconhecido'
    with pytest.raises(titulo_bancario.PoliticaBancariaError):
        _emitir(db, billing_pendente)
    assert len(sicredi.chamadas_de('POST', BOLETOS)) == 1
