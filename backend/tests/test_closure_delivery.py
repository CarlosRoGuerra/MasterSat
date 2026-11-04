"""A closure batch sends exactly its payment titles, independently of list pagination."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.client import Client
from app.models.closure_email_delivery import ClosureEmailDelivery
from app.models.closure_job import ClosureJob
from app.models.enums import BillingStatus, ClientStatus
from app.models.nfse_nota import NfseNota
from app.models.sicredi_boleto import SicrediBoleto

PREFIX = '/api/v1/billing-closure/lotes'


def _client(db, name, document, invoice):
    client = Client(
        name=name, cpf_cnpj=document, type='pf', status=ClientStatus.ACTIVE,
        email=f'{document}@example.test', issue_invoice=invoice,
    )
    db.add(client)
    db.flush()
    return client


def _billing(db, client, *, registered=True, nfse=False):
    billing = Billing(
        client_id=client.id, payer_client_id=client.id, title='Fechamento 09/2026',
        billing_type='boleto_unico', amount=Decimal('64.99'),
        due_date=date(2026, 10, 15), period_label='09/2026',
        status=BillingStatus.PENDING,
    )
    db.add(billing)
    db.flush()
    if registered:
        db.add(AilosBoleto(
            billing_id=billing.id, numero_convenio='123',
            linha_digitavel='1' * 47, codigo_barras='1' * 44,
        ))
    if nfse:
        db.add(NfseNota(billing_id=billing.id, status='emitida', numero_nfse=str(billing.id)))
    return billing


def test_batch_preview_contains_all_titles_and_only_exact_run(http, db):
    with_nfse = _client(db, 'Com nota', '10000000001', 'sim')
    no_nfse = _client(db, 'Sem nota', '10000000002', 'nao')
    undefined = _client(db, 'Sem definição', '10000000003', None)
    included = [_billing(db, with_nfse, nfse=True) for _ in range(30)]
    included += [_billing(db, no_nfse), _billing(db, undefined)]
    missing_bank = _billing(db, no_nfse, registered=False)
    included.append(missing_bank)
    unrelated = _billing(db, no_nfse)
    lote = ClosureJob(
        reference_month='2026-09', status='completed',
        result={'payment_billing_ids': [b.id for b in included]},
    )
    db.add(lote)
    db.commit()

    listed = http.get(PREFIX)
    assert listed.status_code == 200
    assert listed.json()[0]['id'] == lote.id
    assert listed.json()[0]['total_titulos'] == 33
    response = http.get(f'{PREFIX}/{lote.id}')
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['total'] == 33
    assert len(data['itens']) == 33
    assert unrelated.id not in {item['billing_id'] for item in data['itens']}
    assert data['prontos'] == 31
    assert data['bloqueados'] == 2
    assert data['itens'][0]['documentos'] == ['Boleto', 'NFS-e']
    assert data['itens'][30]['documentos'] == ['Boleto']
    assert 'Preferência de NFS-e não informada' in data['itens'][31]['motivo']
    assert 'Boleto não emitido' in data['itens'][32]['motivo']
    assert data['itens'][32]['pode_emitir_boleto'] is True
    assert data['itens'][0]['pode_emitir_boleto'] is False
    assert data['itens'][0]['boleto_banco'] == 'ailos'
    assert http.post(f'{PREFIX}/{lote.id}/enviar/{unrelated.id}').status_code == 404

    additional = _billing(db, no_nfse)
    second_lote = ClosureJob(
        reference_month='2026-09', status='completed',
        result={'payment_billing_ids': [additional.id]},
    )
    db.add(second_lote)
    db.commit()
    exact_batch = http.get('/api/v1/billings/', params={'closure_batch_id': lote.id})
    assert exact_batch.status_code == 200, exact_batch.text
    assert {row['id'] for row in exact_batch.json()} == {b.id for b in included}
    second_batch = http.get('/api/v1/billings/', params={'closure_batch_id': second_lote.id})
    assert {row['id'] for row in second_batch.json()} == {additional.id}
    assert http.get('/api/v1/billings/', params={'closure_batch_id': 99999}).json() == []
    assert http.get('/api/v1/billings/', params={'closure_batch_id': 0}).status_code == 422
    meses = http.get('/api/v1/billing-closure/meses')
    assert meses.status_code == 200
    assert meses.json() == [{'mes_servico': '2026-09', 'total_lotes': 2}]
    por_mes = http.get('/api/v1/billing-closure/meses/2026-09')
    assert por_mes.status_code == 200, por_mes.text
    assert por_mes.json()['total'] == 34
    assert por_mes.json()['total_lotes'] == 2
    ids_do_mes = {item['billing_id'] for item in por_mes.json()['itens']}
    assert ids_do_mes == {*(b.id for b in included), additional.id}
    assert unrelated.id not in ids_do_mes
    assert next(item for item in por_mes.json()['itens'] if item['billing_id'] == additional.id)['lote_id'] == second_lote.id
    assert http.get('/api/v1/billing-closure/meses/2026-13').status_code == 422

    db.query(AilosBoleto).filter_by(billing_id=included[0].id).one().status_ailos = '5'
    db.commit()
    liquidado = http.get(f'{PREFIX}/{lote.id}').json()['itens'][0]
    assert liquidado['estado'] == 'bloqueado'
    assert 'liquidado na Ailos' in liquidado['motivo']


def test_batch_send_attaches_nfse_only_when_required_and_does_not_repeat(http, db, monkeypatch):
    with_nfse = _client(db, 'Com nota', '20000000001', 'sim')
    no_nfse = _client(db, 'Sem nota', '20000000002', 'nao')
    sim = _billing(db, with_nfse, nfse=True)
    nao = _billing(db, no_nfse)
    missing_nfse = _billing(db, with_nfse)
    lote = ClosureJob(
        reference_month='2026-09', status='completed',
        result={'payment_billing_ids': [sim.id, nao.id, missing_nfse.id]},
    )
    db.add(lote)
    db.commit()

    from app.api.v1.endpoints import boletos
    from app.services import email_smtp
    prepared = []
    emails = []

    def prepare(db_, billing_id, include_nfse):
        prepared.append((billing_id, include_nfse))
        attachments = [('boleto.pdf', b'%PDF-boleto', 'application/pdf')]
        if include_nfse:
            attachments.append(('nota.pdf', b'%PDF-nota', 'application/pdf'))
        return 'financeiro@example.test', 'Assunto', 'Corpo', attachments

    monkeypatch.setattr(boletos, 'preparar_envio_boleto_email', prepare)
    monkeypatch.setattr(email_smtp, 'load_config', lambda db_: {'host': 'smtp.test', 'from_email': 'mastersat@example.test'})
    monkeypatch.setattr(email_smtp, 'enviar_email', lambda db_, **kwargs: emails.append(kwargs))

    assert http.post(f'{PREFIX}/{lote.id}/enviar/{missing_nfse.id}').status_code == 409
    from app.services.closure_delivery import processar_fila
    assert http.post(f'{PREFIX}/{lote.id}/enviar/{sim.id}').json()['estado'] == 'aguardando'
    assert emails == []  # O HTTP somente agenda; o worker transmite.
    processar_fila(db)
    assert http.post(f'{PREFIX}/{lote.id}/enviar/{sim.id}').json()['estado'] == 'ja_enviado'
    assert http.post(f'{PREFIX}/{lote.id}/enviar/{nao.id}').json()['estado'] == 'aguardando'
    processar_fila(db)
    assert prepared == [(sim.id, True), (nao.id, False)]
    assert [len(email['anexos']) for email in emails] == [2, 1]
    assert db.query(ClosureEmailDelivery).count() == 2
    assert http.get(f'{PREFIX}/{lote.id}').json()['enviados'] == 2


def _sicredi_boleto(db, billing, **extra):
    boleto = SicrediBoleto(
        billing_id=billing.id, ambiente='sandbox', cooperativa='2602', posto='13',
        codigo_beneficiario='11948', nosso_numero=f'{billing.id:09d}',
        status='REGISTRADO', linha_digitavel='2' * 47, codigo_barras='2' * 44,
    )
    for key, value in extra.items():
        setattr(boleto, key, value)
    db.add(boleto)
    return boleto


def test_closure_recognizes_sicredi_and_queues_its_pdf_without_registering_again(http, db, monkeypatch):
    payer = _client(db, 'Sicredi no fechamento', '51000000001', 'nao')
    billing = _billing(db, payer, registered=False)
    _sicredi_boleto(db, billing)
    lote = ClosureJob(reference_month='2026-09', status='completed',
                      result={'payment_billing_ids': [billing.id]})
    db.add(lote)
    db.commit()

    item = http.get(f'{PREFIX}/{lote.id}').json()['itens'][0]
    assert item['estado'] == 'pronto' and item['motivo'] is None
    assert item['boleto_emitido'] is True
    assert item['boleto_banco'] == 'sicredi'
    assert item['titulo_bancario']['canal'] == 'sicredi_api'
    assert item['pode_emitir_boleto'] is False

    from app.api.v1.endpoints import boletos
    from app.services import email_smtp, sicredi_boletos
    from app.services.closure_delivery import processar_fila
    sent = []
    prepared = []

    def sicredi_pdf(b, c, db_):
        prepared.append(b.id)
        return b'%PDF-sicredi', 'sicredi.pdf'

    def unexpected_registration(*args, **kwargs):
        pytest.fail('Conferir/enviar fechamento não pode registrar boleto no banco')

    monkeypatch.setattr(boletos, '_pdf_sicredi', sicredi_pdf)
    monkeypatch.setattr(sicredi_boletos, 'gerar_boleto', unexpected_registration)
    monkeypatch.setattr(email_smtp, 'load_config', lambda _: {'host': 'smtp.test', 'from_email': 'sender@example.test'})
    monkeypatch.setattr(email_smtp, 'enviar_email', lambda db_, **kwargs: sent.append(kwargs))
    result = http.post(f'{PREFIX}/{lote.id}/enviar', json={'billing_ids': [billing.id]})
    assert result.status_code == 202, result.text
    assert result.json()['enfileirados'] == [billing.id]
    assert sent == [] and prepared == []
    processar_fila(db)
    assert prepared == [billing.id]
    assert sent[0]['anexos'] == [('sicredi.pdf', b'%PDF-sicredi', 'application/pdf')]
    assert db.query(AilosBoleto).count() == 0
    assert db.query(SicrediBoleto).count() == 1


@pytest.mark.parametrize('extra', [
    {'baixa_status': 'pendente'}, {'baixa_status': 'solicitada'}, {'baixa_status': 'confirmada'},
    {'status': 'REGISTRANDO'}, {'status': 'DESFECHO_DESCONHECIDO'},
    {'status': 'NA_FILA'}, {'linha_digitavel': None},
])
def test_closure_blocks_sicredi_with_pending_registration_or_cancellation(http, db, monkeypatch, extra):
    payer = _client(db, 'Sicredi pendente', '52000000001', 'nao')
    billing = _billing(db, payer, registered=False)
    _sicredi_boleto(db, billing, **extra)
    lote = ClosureJob(reference_month='2026-09', status='completed',
                      result={'payment_billing_ids': [billing.id]})
    db.add(lote)
    db.commit()
    item = http.get(f'{PREFIX}/{lote.id}').json()['itens'][0]
    assert item['estado'] == 'bloqueado'
    assert item['pode_emitir_boleto'] is False
    from app.services import email_smtp
    monkeypatch.setattr(email_smtp, 'load_config', lambda _: {'host': 'smtp.test', 'from_email': 'sender@example.test'})
    result = http.post(f'{PREFIX}/{lote.id}/enviar', json={'billing_ids': [billing.id]})
    assert result.json()['enfileirados'] == []
    assert db.query(ClosureEmailDelivery).count() == 0


def test_closure_issuance_eligibility_excludes_closed_substituted_deleted_and_system_only(http, db):
    payer = _client(db, 'Sem boleto', '53000000001', 'sim')
    billings = [_billing(db, payer, registered=False) for _ in range(6)]
    billings[1].status = BillingStatus.PAID
    billings[2].status = BillingStatus.CANCELED
    billings[3].is_deleted = True
    billings[4].substituted_by_id = billings[0].id
    billings[4].status = BillingStatus.CANCELED
    billings[5].somente_sistema = True
    lote = ClosureJob(reference_month='2026-09', status='completed',
                      result={'payment_billing_ids': [b.id for b in billings]})
    db.add(lote)
    db.commit()
    items = http.get(f'{PREFIX}/{lote.id}').json()['itens']
    assert [item['pode_emitir_boleto'] for item in items] == [True, False, False, False, False, False]
    assert 'NFS-e obrigatória' in items[0]['motivo']


def test_recovers_older_closure_without_carnets_or_duplicate_batches(http, db):
    client = _client(db, 'Fechamento antigo', '30000000001', 'nao')
    individual = _client(db, 'Faturas individuais', '30000000002', 'nao')
    momento = datetime(2026, 10, 2, 21, 7, 50, 272814, tzinfo=timezone.utc)
    antigo = _billing(db, client)
    componente = _billing(db, client)
    avulsa = _billing(db, client)
    carne = _billing(db, client)
    individuais = [_billing(db, individual) for _ in range(29)]
    for billing in (antigo, componente):
        billing.created_at = momento
    for billing in individuais:
        billing.created_at = momento
        billing.billing_type = 'recorrente'
        billing.period_label = '10/2026'  # Vence em outubro, mas pertence ao fechamento de setembro.
    antigo.title = 'Fechamento 09/2026 - boleto único'
    componente.status = BillingStatus.CANCELED
    componente.substituted_by_id = antigo.id
    avulsa.billing_type = 'avulsa'
    carne.billing_type = 'carne'
    db.commit()

    first = http.post(PREFIX + '/recuperar')
    assert first.status_code == 200, first.text
    assert len(first.json()) == 1
    lote = first.json()[0]
    assert lote['mes_servico'] == '2026-09'
    assert lote['recuperado'] is True
    assert lote['total_titulos'] == 30
    preview = http.get(f"{PREFIX}/{lote['id']}").json()
    assert len(preview['itens']) == 30
    assert {item['billing_id'] for item in preview['itens']} == {antigo.id, *(b.id for b in individuais)}
    assert avulsa.id not in [item['billing_id'] for item in preview['itens']]
    assert carne.id not in [item['billing_id'] for item in preview['itens']]
    setembro = http.get('/api/v1/billings/', params={'closure_month': '2026-09'})
    assert setembro.status_code == 200, setembro.text
    assert {item['id'] for item in setembro.json()} == {antigo.id, *(b.id for b in individuais)}
    assert http.get('/api/v1/billings/', params={'closure_month': '2026-10'}).json() == []
    second = http.post(PREFIX + '/recuperar')
    assert second.status_code == 200
    assert second.json() == first.json()
    assert db.query(ClosureJob).count() == 1


def test_partial_smtp_acceptance_is_not_success_or_automatically_retried(http, db, monkeypatch):
    from app.api.v1.endpoints import boletos
    from app.services import email_smtp

    payer = _client(db, 'Pagador', '40000000001', 'nao')
    payer.email = None
    payer.extra_emails = ['financeiro@example.test', 'recusado@example.test']
    owner = _client(db, 'Proprietário', '40000000002', 'nao')
    billing = _billing(db, payer)
    billing.client_id = owner.id
    lote = ClosureJob(reference_month='2026-09', status='completed',
                      result={'payment_billing_ids': [billing.id]})
    db.add(lote)
    db.commit()
    preview = http.get(f'{PREFIX}/{lote.id}').json()['itens'][0]
    assert preview['estado'] == 'pronto'
    assert preview['emails'] == payer.extra_emails
    assert owner.email not in preview['email']

    monkeypatch.setattr(boletos, '_montar_pdf_boleto', lambda *args: (b'%PDF-test', 'boleto.pdf'))
    monkeypatch.setattr(email_smtp, 'load_config', lambda _: {
        'host': 'smtp.test', 'from_email': 'envio@example.test', 'from_name': '',
    })
    transmissions = []
    closed = []

    class Server:
        def send_message(self, message):
            transmissions.append(message)
            return {'recusado@example.test': (550, b'Rejected')}

        def quit(self):
            closed.append(True)

    monkeypatch.setattr(email_smtp, '_abrir_conexao', lambda _: Server())
    url = f'{PREFIX}/{lote.id}/enviar/{billing.id}'
    result = http.post(url)
    assert result.status_code == 202, result.text
    from app.services.closure_delivery import processar_fila
    processar_fila(db)
    assert 'recusado@example.test' in db.query(ClosureEmailDelivery).one().error
    assert db.query(ClosureEmailDelivery).one().status == 'desconhecido'
    assert http.get(f'{PREFIX}/{lote.id}').json()['indeterminados'] == 1
    assert http.post(url).status_code == 409
    assert len(transmissions) == 1
    assert str(transmissions[0]['To']) == ', '.join(payer.extra_emails)
    assert closed == [True]
