"""A closure batch sends exactly its payment titles, independently of list pagination."""
from datetime import date
from decimal import Decimal

from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.client import Client
from app.models.closure_email_delivery import ClosureEmailDelivery
from app.models.closure_job import ClosureJob
from app.models.enums import BillingStatus, ClientStatus
from app.models.nfse_nota import NfseNota

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
    assert 'Boleto Ailos não emitido' in data['itens'][32]['motivo']
    assert http.post(f'{PREFIX}/{lote.id}/enviar/{unrelated.id}').status_code == 404

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
    assert http.post(f'{PREFIX}/{lote.id}/enviar/{sim.id}').json()['estado'] == 'enviado'
    assert http.post(f'{PREFIX}/{lote.id}/enviar/{sim.id}').json()['estado'] == 'ja_enviado'
    assert http.post(f'{PREFIX}/{lote.id}/enviar/{nao.id}').json()['estado'] == 'enviado'
    assert prepared == [(sim.id, True), (nao.id, False)]
    assert [len(email['anexos']) for email in emails] == [2, 1]
    assert db.query(ClosureEmailDelivery).count() == 2
    assert http.get(f'{PREFIX}/{lote.id}').json()['enviados'] == 2
