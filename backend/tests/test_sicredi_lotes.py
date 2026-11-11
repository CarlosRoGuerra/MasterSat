"""Lote/carnê Sicredi usa a mesma emissão idempotente e fila recuperável."""
import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import requests
from pypdf import PdfReader

from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus
from app.models.sicredi_boleto import SicrediBoleto
from app.models.sicredi_lote import SicrediLote
from app.services import sicredi_boletos, sicredi_lotes, titulo_bancario
from tests.fase03_apoio import registrar_titulo, reserva
from tests.sicredi_apoio import Resp, resposta_cadastro, sicredi  # noqa: F401

BOLETOS = '/cobranca/boleto/v1/boletos'


@pytest.fixture()
def parcelas(db, cliente):
    itens = [Billing(client_id=cliente.id, payer_client_id=cliente.id, title=f'Parcela {i}',
                     billing_type='recorrente', installment_number=i, installment_total=3,
                     amount=Decimal('99.90'), due_date=date(2099, 10, 15) + timedelta(days=i * 31),
                     status=BillingStatus.PENDING) for i in range(1, 4)]
    db.add_all(itens)
    db.commit()
    return itens


def eco(**kwargs):
    return resposta_cadastro(kwargs['json']['nossoNumero'])


def test_criacao_reserva_todas_as_parcelas_e_bloqueia_mutacoes_antes_da_rede(db, parcelas, sicredi):
    lote = sicredi_lotes.criar_lote(db, [b.id for b in parcelas])
    assert db.query(SicrediBoleto).count() == 3
    assert all(b.status == 'NA_FILA' and b.lote_id == lote.id for b in db.query(SicrediBoleto).all())
    assert sicredi.chamadas == []
    for operacao in (titulo_bancario.EMITIR_AILOS, titulo_bancario.EMITIR_SICREDI,
                     titulo_bancario.ALTERAR_VALOR, titulo_bancario.RECEBER, titulo_bancario.CANCELAR):
        with pytest.raises(titulo_bancario.PoliticaBancariaError):
            titulo_bancario.exigir(db, operacao, [parcelas[0].id])
    assert sicredi_boletos.resolver_desfecho(db, parcelas[0].id)['resultado'] == 'aguardar'
    assert sicredi.chamadas == []


def test_repetir_criacao_reutiliza_mesmo_lote_e_numeros(db, parcelas, sicredi):
    ids = [b.id for b in parcelas]
    a = sicredi_lotes.criar_lote(db, ids)
    numeros = [b.nosso_numero for b in db.query(SicrediBoleto).all()]
    b = sicredi_lotes.criar_lote(db, [*reversed(ids), ids[0]])
    assert a.id == b.id and db.query(SicrediLote).count() == 1
    assert numeros == [b.nosso_numero for b in db.query(SicrediBoleto).all()]


@pytest.mark.parametrize('legacy_order', [False, True])
def test_emissao_e_retomada_respeitam_vencimento_mesmo_com_ids_fora_de_ordem(
    db, parcelas, sicredi, legacy_order,
):
    parcelas[-1].due_date = date(2099, 10, 15)
    db.commit()
    ids = [b.id for b in parcelas]
    esperada = [ids[2], ids[0], ids[1]]
    lote = sicredi_lotes.criar_lote(db, ids)
    assert lote.billing_ids == esperada
    if legacy_order:
        lote.billing_ids = ids
        db.commit()
    numeros = {r.billing_id: r.nosso_numero for r in db.query(SicrediBoleto).all()}
    sicredi.quando('POST', BOLETOS, eco)
    # Mesmo o subconjunto retomado deve seguir a ordem do carnê, não a dos IDs.
    status = sicredi_lotes.processar_lote(db, lote.id, billing_ids=[ids[1], ids[2]], retentar=True)
    assert [p['billing_id'] for p in status['parcelas']] == esperada
    assert [p['numero_parcela'] for p in status['parcelas']] == [1, 2, 3]
    assert [p['status'] for p in status['parcelas']] == ['registrado', 'processando', 'registrado']
    assert [c['json']['nossoNumero'] for c in sicredi.chamadas_de('POST', BOLETOS)] == [
        numeros[ids[2]], numeros[ids[1]],
    ]
    sicredi_lotes.processar_lote(db, lote.id)
    assert [c['json']['nossoNumero'] for c in sicredi.chamadas_de('POST', BOLETOS)] == [
        numeros[ids[2]], numeros[ids[1]], numeros[ids[0]],
    ]
    sicredi_lotes.processar_lote(db, lote.id, retentar=True)
    assert len(sicredi.chamadas_de('POST', BOLETOS)) == 3


def test_lote_avulso_preserva_ordem_anterior_sem_renumerar_por_vencimento(db, parcelas, sicredi):
    parcelas[-1].due_date = date(2099, 10, 15)
    db.commit()
    ids = [b.id for b in parcelas]
    lote = sicredi_lotes.criar_lote(db, list(reversed(ids)), tipo='boleto')
    assert lote.billing_ids == ids
    assert [p['billing_id'] for p in sicredi_lotes.status_lote(db, lote)['parcelas']] == ids
    assert sicredi.chamadas == []


@pytest.mark.parametrize('tipo', ['carne', 'boleto'])
def test_endpoint_progresso_registro_e_repeticao_sem_duplicar(db, http, parcelas, sicredi, tipo):
    sicredi.quando('POST', BOLETOS, eco)
    path = '/api/v1/sicredi/carne/lote' if tipo == 'carne' else '/api/v1/sicredi/boletos/lote'
    r = http.post(path, json={'billing_ids': [b.id for b in parcelas]})
    assert r.status_code == 200, r.text
    lote = r.json()
    resumo = http.get(f"/api/v1/sicredi/lotes/{lote['ticket']}")
    assert resumo.status_code == 200, resumo.text
    assert resumo.json()['status'] == 'completed' and resumo.json()['prontas'] == 3
    assert http.post(path, json={'billing_ids': [b.id for b in parcelas]}).json()['id'] == lote['id']
    assert len(sicredi.chamadas_de('POST', BOLETOS)) == 3


def test_falha_parcial_nao_interrompe_lote_e_retry_envia_apenas_faltante(db, http, parcelas, sicredi):
    sicredi.quando('POST', BOLETOS, eco, Resp(422, {'mensagem': 'CEP inválido'}), eco)
    lote = http.post('/api/v1/sicredi/carne/lote', json={'billing_ids': [b.id for b in parcelas]}).json()
    resumo = http.get(f"/api/v1/sicredi/lotes/{lote['ticket']}").json()
    assert resumo['prontas'] == 2
    assert [p['status'] for p in resumo['parcelas']] == ['registrado', 'erro', 'registrado']
    assert 'CEP' in resumo['parcelas'][1]['erro']
    path = f"/api/v1/boletos/carne/{lote['id']}/pdf?banco=sicredi"
    assert http.get(path).status_code == 409
    parcial = http.get(path + '&parcial=true')
    assert parcial.status_code == 200
    texto = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(parcial.content)).pages)
    assert texto.count('748-X') == 2 and '085-0' not in texto
    retry = http.post(f"/api/v1/sicredi/lotes/{lote['id']}/registrar-pendentes")
    assert retry.status_code == 200 and retry.json()['status'] == 'processing'
    assert len(sicredi.chamadas_de('POST', BOLETOS)) == 4
    assert http.get(path).status_code == 200


def test_timeout_exige_consulta_antes_de_retentar_sem_reenviar_as_confirmadas(db, http, parcelas, sicredi):
    sicredi.quando('POST', BOLETOS, requests.exceptions.ReadTimeout('timeout'), eco)
    lote = http.post('/api/v1/sicredi/carne/lote', json={'billing_ids': [b.id for b in parcelas]}).json()
    sicredi.quando('GET', BOLETOS, Resp(404, {'message': 'Boleto não encontrado'}))
    http.post(f"/api/v1/sicredi/lotes/{lote['id']}/registrar-pendentes")
    assert len(sicredi.chamadas_de('POST', BOLETOS)) == 3
    registro = db.query(SicrediBoleto).filter_by(billing_id=parcelas[0].id).one()
    numero = registro.nosso_numero
    registro.registro_iniciado_em = datetime.now(timezone.utc) - timedelta(minutes=30)
    db.commit()
    http.post(f"/api/v1/sicredi/lotes/{lote['id']}/parcelas/{parcelas[0].id}/registrar")
    assert len(sicredi.chamadas_de('POST', BOLETOS)) == 4
    assert sicredi.chamadas_de('POST', BOLETOS)[-1]['json']['nossoNumero'] == numero


def test_fila_retoma_somente_parcelas_pendentes_apos_interrupcao(db, parcelas, sicredi):
    sicredi.quando('POST', BOLETOS, eco)
    lote = sicredi_lotes.criar_lote(db, [b.id for b in parcelas])
    assert sicredi_lotes.processar_lote(db, lote.id, limite=1)['prontas'] == 1
    assert db.query(SicrediBoleto).filter_by(status='NA_FILA').count() == 2
    sicredi_lotes.processar_fila(db)
    assert sicredi_lotes.status_lote(db, lote)['prontas'] == 3
    assert len(sicredi.chamadas_de('POST', BOLETOS)) == 3


def test_carteira_lista_carne_sicredi_e_pdf_nao_chama_o_banco(db, http, parcelas, sicredi):
    sicredi.quando('POST', BOLETOS, eco)
    lote = http.post('/api/v1/sicredi/carne/lote', json={'billing_ids': [b.id for b in parcelas]}).json()
    resultado = http.get(f'/api/v1/boletos/carne?payer_client_id={parcelas[0].client_id}').json()
    assert len(resultado) == 1 and resultado[0]['banco'] == 'sicredi'
    assert resultado[0]['parcelas_registradas'] == 3
    antes = len(sicredi.chamadas)
    pdf = http.get(f"/api/v1/boletos/carne/{lote['id']}/pdf?banco=sicredi")
    assert pdf.status_code == 200 and len(sicredi.chamadas) == antes
    texto = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert texto.count('748-X') == 3


@pytest.mark.parametrize('cenario', ['paga', 'cancelada', 'removida', 'somente_sistema', 'ailos', 'reserva_ailos', 'vencida_data'])
def test_lote_recusa_cobranca_inapta_atomicamente(db, http, parcelas, sicredi, cenario):
    b = parcelas[-1]
    if cenario == 'paga': b.status = BillingStatus.PAID
    elif cenario == 'cancelada': b.status = BillingStatus.CANCELED
    elif cenario == 'removida': b.is_deleted = True
    elif cenario == 'somente_sistema': b.somente_sistema = True
    elif cenario == 'ailos': registrar_titulo(db, b)
    elif cenario == 'reserva_ailos': reserva(db, b, 'REGISTRANDO')
    else: b.due_date = date(2020, 1, 1)
    db.commit()
    r = http.post('/api/v1/sicredi/carne/lote', json={'billing_ids': [b.id for b in parcelas]})
    assert r.status_code in (409, 422), r.text
    assert db.query(SicrediLote).count() == 0 and db.query(SicrediBoleto).count() == 0
    assert sicredi.chamadas == []


def test_reserva_de_lote_nao_pode_sobrepor_registro_ailos_concorrente(db, parcelas, sicredi):
    lote = sicredi_lotes.criar_lote(db, [b.id for b in parcelas])
    reserva(db, parcelas[0], 'REGISTRANDO')
    with pytest.raises(titulo_bancario.PoliticaBancariaError):
        sicredi_boletos.gerar_boleto(db, parcelas[0].id, lote_id=lote.id)
    assert sicredi.chamadas == []


def test_operacional_nao_emite_lotes(http_op, parcelas, sicredi):
    for path in ('carne/lote', 'boletos/lote'):
        assert http_op.post('/api/v1/sicredi/' + path, json={'billing_ids': [b.id for b in parcelas]}).status_code == 403


def test_carne_exige_dois_ids_distintos_e_retry_confere_vinculo(db, http, parcelas, sicredi):
    assert http.post('/api/v1/sicredi/carne/lote', json={'billing_ids': [parcelas[0].id] * 2}).status_code == 422
    lote = sicredi_lotes.criar_lote(db, [b.id for b in parcelas[:2]])
    assert http.post(f'/api/v1/sicredi/lotes/{lote.id}/parcelas/{parcelas[2].id}/registrar').status_code == 404


def test_lote_agrega_clientes_mas_carne_exige_um_pagador(db, http, parcelas, sicredi):
    cliente_pj = Client(name='CLIENTE LOTE', cpf_cnpj='11222333000181', type='pj',
                        zip_code='89201000', address_line='Rua Teste', address_number='100',
                        neighborhood='Centro', city='Joinville', state='SC')
    db.add(cliente_pj)
    db.flush()
    parcelas[-1].client_id = cliente_pj.id
    parcelas[-1].payer_client_id = cliente_pj.id
    db.commit()
    ids = [b.id for b in parcelas]
    assert http.post('/api/v1/sicredi/carne/lote', json={'billing_ids': ids}).status_code == 422
    assert db.query(SicrediLote).count() == 0
    sicredi.quando('POST', BOLETOS, eco)
    r = http.post('/api/v1/sicredi/boletos/lote', json={'billing_ids': ids})
    assert r.status_code == 200, r.text
    assert len(sicredi.chamadas_de('POST', BOLETOS)) == 3


def test_carne_reaproveita_titulo_registrado_e_fixa_pagador_da_reserva(db, http, parcelas, sicredi):
    sicredi.quando('POST', BOLETOS, eco)
    sicredi_boletos.gerar_boleto(db, parcelas[0].id)
    parcelas[1].payer_client_id = None
    db.commit()
    lote = sicredi_lotes.criar_lote(db, [b.id for b in parcelas])
    db.refresh(parcelas[1])
    assert parcelas[1].payer_client_id == parcelas[1].client_id
    sicredi_lotes.processar_lote(db, lote.id)
    assert len(sicredi.chamadas_de('POST', BOLETOS)) == 3
    for b in parcelas:
        b.is_deleted = True
    db.commit()
    assert http.get(f'/api/v1/boletos/carne?payer_client_id={parcelas[0].client_id}').json() == []


def test_parcela_paga_nao_trava_download_das_abertas_nem_pede_novo_registro(db, http, parcelas, sicredi):
    sicredi.quando('POST', BOLETOS, eco)
    lote = http.post('/api/v1/sicredi/carne/lote', json={'billing_ids': [b.id for b in parcelas]}).json()
    parcelas[0].status = BillingStatus.PAID
    db.commit()
    st = http.get(f"/api/v1/sicredi/lotes/{lote['ticket']}").json()
    assert st['status'] == 'completed' and st['prontas'] == 3
    assert st['parcelas'][0]['status'] == 'registrado'
    pdf = http.get(f"/api/v1/boletos/carne/{lote['id']}/pdf?banco=sicredi")
    assert pdf.status_code == 200, pdf.text
    assert sum(p.extract_text().count('748-X') for p in PdfReader(io.BytesIO(pdf.content)).pages) == 2
    http.post(f"/api/v1/sicredi/lotes/{lote['id']}/registrar-pendentes")
    assert len(sicredi.chamadas_de('POST', BOLETOS)) == 3
