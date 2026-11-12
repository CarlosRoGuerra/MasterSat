"""Fechamento de carnês: um e-mail por carnê, com o PDF das 12 parcelas."""
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest

from app.models.closure_email_delivery import ClosureEmailDelivery
from app.models.contract import Contract
from app.models.nfse_nota import NfseNota
from tests.sicredi_apoio import resposta_cadastro, sicredi  # noqa: F401

PREFIX = '/api/v1/billing-closure'
PARAMS = {'service_month': '2026-11', 'forma_cobranca': 'carne_sicredi', 'carne_installments': 12}


@pytest.fixture
def fechamento(http, db, cliente, plan):
    cliente.forma_cobranca = 'carne_sicredi'
    cliente.boleto_format = 'unico'
    cliente.issue_invoice = 'nao'
    plan.price = Decimal('64.99')
    db.add(Contract(client_id=cliente.id, plan_id=plan.id, start_date=date(2026, 1, 1),
                    status='ativo', billing_day=10))
    db.commit()
    result = http.post(PREFIX + '/generate', params=PARAMS)
    assert result.status_code == 200, result.text
    return f"{PREFIX}/lotes/{result.json()['closure_batch_id']}"


@pytest.fixture
def smtp(monkeypatch):
    from app.services import email_smtp
    enviados = []
    monkeypatch.setattr(email_smtp, 'load_config', lambda _: {'host': 'smtp.test', 'from_email': 'mastersat@test.local'})
    monkeypatch.setattr(email_smtp, 'enviar_email', lambda db_, **kwargs: enviados.append(kwargs))
    return enviados


def _emitir_carne(http, sicredi, ids):
    sicredi.quando('POST', '/cobranca/boleto/v1/boletos', lambda **kw: resposta_cadastro(kw['json']['nossoNumero']))
    response = http.post('/api/v1/sicredi/carne/lote', json={'billing_ids': ids})
    assert response.status_code == 200, response.text


def test_carne_vai_num_unico_email_com_as_doze_parcelas(http, db, fechamento, sicredi, smtp):
    from pypdf import PdfReader
    from app.services.closure_delivery import processar_fila

    previa = http.get(fechamento).json()
    ids = previa['carnes'][0]['billing_ids']
    assert previa['total'] == 1 and len(previa['itens']) == 1
    item = previa['itens'][0]
    assert item['billing_id'] == ids[0] and item['documentos'] == ['Carnê (12 boletos)']
    assert item['valor'] == pytest.approx(779.88)
    assert item['estado'] == 'bloqueado' and 'Carnê ainda não emitido' in item['motivo']

    _emitir_carne(http, sicredi, ids)
    item = http.get(fechamento).json()['itens'][0]
    assert item['estado'] == 'pronto', item['motivo']

    # Uma parcela seguinte nunca sai sozinha: vai no e-mail do carnê.
    sozinha = http.post(f'{fechamento}/enviar/{ids[1]}')
    assert sozinha.status_code == 409 and 'e-mail do carnê' in sozinha.json()['detail']

    fila = http.post(f'{fechamento}/enviar', json={'billing_ids': [ids[0]]})
    assert fila.status_code == 202, fila.text
    assert fila.json()['enfileirados'] == [ids[0]]
    processar_fila(db)
    processar_fila(db)
    assert len(smtp) == 1
    email = smtp[0]
    assert email['destinatario'] == 'joao@test.local'
    assert email['assunto'] == 'Carnê MasterSat — 12/2026 a 11/2027'
    assert '12 parcela(s)' in email['corpo'] and 'R$ 779,88' in email['corpo']
    [(nome, pdf, tipo)] = email['anexos']
    assert nome.endswith('.pdf') and tipo == 'application/pdf'
    paginas = PdfReader(BytesIO(pdf)).pages
    assert len(paginas) == 4 and [p.extract_text().count('748-X') for p in paginas] == [3] * 4
    assert db.query(ClosureEmailDelivery).count() == 1
    previa = http.get(fechamento).json()
    assert previa['enviados'] == 1 and previa['itens'][0]['estado'] == 'enviado'


def test_carne_com_registro_incompleto_nao_e_enviado(http, db, fechamento, sicredi, smtp):
    ids = http.get(fechamento).json()['carnes'][0]['billing_ids']
    sicredi.quando('POST', '/cobranca/boleto/v1/boletos', lambda **kw: (
        resposta_cadastro(kw['json']['nossoNumero']) if int(kw['json']['seuNumero']) != ids[5]
        else (_ for _ in ()).throw(RuntimeError('fora do ar'))))
    http.post('/api/v1/sicredi/carne/lote', json={'billing_ids': ids})
    item = http.get(fechamento).json()['itens'][0]
    assert item['estado'] == 'bloqueado'
    assert 'Registro do carnê em andamento: 11 de 12' in item['motivo']
    assert http.post(f'{fechamento}/enviar', json={'billing_ids': [ids[0]]}).json()['enfileirados'] == []
    assert smtp == []


def test_nfse_do_carne_e_so_a_da_primeira_parcela(http, db, cliente, fechamento, sicredi, smtp, monkeypatch):
    from app.services.closure_delivery import processar_fila
    from app.api.v1.endpoints import nfse
    cliente.issue_invoice = 'sim'
    db.commit()
    ids = http.get(fechamento).json()['carnes'][0]['billing_ids']
    _emitir_carne(http, sicredi, ids)
    item = http.get(fechamento).json()['itens'][0]
    assert item['documentos'] == ['Carnê (12 boletos)', 'NFS-e']
    assert 'NFS-e da 1ª parcela ainda não emitida' in item['motivo']
    db.add(NfseNota(billing_id=ids[0], status='emitida', numero_nfse='1'))
    db.commit()
    assert http.get(fechamento).json()['itens'][0]['estado'] == 'pronto'
    anexados = []

    def nota(db_, billing_id):
        anexados.append(billing_id)
        return ('nfse.pdf', b'%PDF-nota', 'application/pdf')

    monkeypatch.setattr(nfse, 'anexo_nfse_emitida', nota)
    http.post(f'{fechamento}/enviar', json={'billing_ids': [ids[0]]})
    processar_fila(db)
    assert anexados == [ids[0]]
    assert [nome for nome, _, _ in smtp[0]['anexos']][1] == 'nfse.pdf'
    assert 'NFS-e da primeira parcela' in smtp[0]['corpo']
