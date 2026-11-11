from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.models.billing import Billing
from app.models.client_charge_item import ClientChargeItem
from app.models.closure_job import ClosureJob
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.models.sicredi_boleto import SicrediBoleto
from app.services.financial import add_months
from tests.sicredi_apoio import resposta_cadastro, sicredi  # noqa: F401

PREFIX = '/api/v1/billing-closure'
PARAMS = {'service_month': '2026-11', 'forma_cobranca': 'carne_sicredi', 'carne_installments': 12}


@pytest.fixture
def monthly(db, cliente, plan):
    cliente.forma_cobranca = 'carne_sicredi'
    cliente.boleto_format = 'unico'
    plan.price = Decimal('64.99')
    contract = Contract(client_id=cliente.id, plan_id=plan.id, start_date=date(2026, 1, 1),
                        status='ativo', billing_day=10)
    db.add(contract)
    db.commit()
    return contract


def test_preview_shows_twelve_installments_without_creating_or_emitting(http, db, monthly):
    response = http.get(PREFIX + '/simulate', params=PARAMS)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['total_contracts'] == 1
    assert preview['carne_installments'] == 12
    assert preview['to_generate'] == 12
    assert preview['total_amount'] == 779.88
    assert [row['due_date'] for row in preview['items']] == [
        add_months(date(2026, 12, 10), i).isoformat() for i in range(12)]
    assert [row['installment_number'] for row in preview['items']] == list(range(1, 13))
    assert {row['installment_total'] for row in preview['items']} == {12}
    assert preview['items'][0]['service_period_label'] == '11/2026'
    assert preview['items'][-1]['service_period_label'] == '10/2027'
    assert db.query(Billing).count() == db.query(SicrediBoleto).count() == 0


def test_generate_preserves_twelve_payable_titles_and_is_idempotent(http, db, monthly):
    params = {**PARAMS, 'contract_ids': monthly.id, 'uninstall_event_ids': -1, 'charge_item_ids': -1}
    response = http.post(PREFIX + '/generate', params=params)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['payment_titles_generated'] == result['generated'] == 12
    assert result['grand_total'] == 779.88
    titles = list(db.scalars(select(Billing).where(Billing.id.in_(result['payment_billing_ids']))
                            .order_by(Billing.due_date)))
    assert len(titles) == 12
    assert {title.billing_type for title in titles} == {'carne'}
    assert {title.amount for title in titles} == {Decimal('64.99')}
    assert [title.installment_number for title in titles] == list(range(1, 13))
    assert titles[-1].due_date == date(2027, 11, 10)
    assert db.get(ClosureJob, result['closure_batch_id']).result['payment_billing_ids'] == result['payment_billing_ids']
    assert db.query(SicrediBoleto).count() == 0
    again = http.post(PREFIX + '/generate', params=params)
    assert again.status_code == 200, again.text
    assert again.json()['generated'] == 0
    assert db.query(Billing).count() == 12


def test_closure_uses_existing_carne_flow_for_twelve_independent_titles_and_four_pdf_pages(
    http, db, monthly, sicredi,
):
    from pypdf import PdfReader
    from app.models.sicredi_lote import SicrediLote

    result = http.post(PREFIX + '/generate', params=PARAMS).json()
    ids = result['payment_billing_ids']
    path = f"{PREFIX}/lotes/{result['closure_batch_id']}"
    group = http.get(path).json()['carnes'][0]
    assert group['billing_ids'] == ids and group['total'] == 12
    assert group['registradas'] == 0 and group['pode_iniciar']
    assert group['lote_id'] is None
    assert db.query(SicrediLote).count() == db.query(SicrediBoleto).count() == 0
    assert sicredi.chamadas == []  # Abrir/conferir não emite nada.

    sicredi.quando('POST', '/cobranca/boleto/v1/boletos',
                  lambda **kw: resposta_cadastro(kw['json']['nossoNumero']))
    response = http.post('/api/v1/sicredi/carne/lote', json={'billing_ids': ids})
    assert response.status_code == 200, response.text
    lote = response.json()
    group = http.get(path).json()['carnes'][0]
    assert group['lote_id'] == lote['id'] and group['ticket'] == lote['ticket']
    assert group['registradas'] == 12 and not group['pode_iniciar']
    records = db.query(SicrediBoleto).all()
    assert len(records) == len({r.nosso_numero for r in records}) == 12
    assert len(sicredi.chamadas_de('POST', '/cobranca/boleto/v1/boletos')) == 12
    pdf = http.get(f"/api/v1/boletos/carne/{lote['id']}/pdf?banco=sicredi")
    assert pdf.status_code == 200, pdf.text
    pages = PdfReader(BytesIO(pdf.content)).pages
    assert len(pages) == 4
    assert [p.extract_text().count('748-X') for p in pages] == [3] * 4
    before = len(sicredi.chamadas)
    repeated = http.post('/api/v1/sicredi/carne/lote', json={'billing_ids': ids})
    assert repeated.json()['id'] == lote['id']
    assert len(sicredi.chamadas) == before and db.query(Billing).count() == 12


def test_existing_individually_registered_titles_can_become_carne_without_new_bank_calls(
    http, db, monthly, sicredi,
):
    from app.services import sicredi_boletos
    from app.models.sicredi_lote import SicrediLote

    result = http.post(PREFIX + '/generate', params=PARAMS).json()
    ids = result['payment_billing_ids']
    sicredi.quando('POST', '/cobranca/boleto/v1/boletos',
                  lambda **kw: resposta_cadastro(kw['json']['nossoNumero']))
    for bid in ids:
        sicredi_boletos.gerar_boleto(db, bid)
    before = len(sicredi.chamadas)
    numbers = {r.billing_id: r.nosso_numero for r in db.query(SicrediBoleto).all()}
    group = http.get(f"{PREFIX}/lotes/{result['closure_batch_id']}").json()['carnes'][0]
    assert group['registradas'] == 12 and group['pode_iniciar'] and group['lote_id'] is None
    assert db.query(SicrediLote).count() == 0
    response = http.post('/api/v1/sicredi/carne/lote', json={'billing_ids': ids})
    assert response.status_code == 200, response.text
    assert len(sicredi.chamadas) == before  # Só monta o carnê com os dados oficiais existentes.
    assert {r.billing_id: r.nosso_numero for r in db.query(SicrediBoleto).all()} == numbers
    assert db.query(Billing).count() == 12


@pytest.mark.parametrize('legacy_order', [False, True])
def test_first_month_with_service_keeps_chronological_order_in_tracking_and_pdf(
    http, db, monthly, cliente, sicredi, monkeypatch, legacy_order,
):
    from pypdf import PdfReader
    from app.models.sicredi_lote import SicrediLote
    from app.services import boleto_pdf

    db.add(ClientChargeItem(client_id=cliente.id, title='Servico do primeiro mes', quantity=1,
                            unit_price=Decimal('10'), total_amount=Decimal('10'),
                            installment_count=1, start_date=date(2026, 11, 1), active=True))
    db.commit()
    generated = http.post(PREFIX + '/generate', params=PARAMS)
    assert generated.status_code == 200, generated.text
    result = generated.json()
    group = http.get(f"{PREFIX}/lotes/{result['closure_batch_id']}").json()['carnes'][0]
    ids = group['billing_ids']
    assert len(ids) == 12 and ids[0] == max(ids)  # A primeira parcela consolidada é criada por último.
    titles = [db.get(Billing, bid) for bid in ids]
    assert titles[0].amount == Decimal('74.99')
    assert [b.due_date for b in titles] == [add_months(date(2026, 12, 10), i) for i in range(12)]
    sicredi.quando('POST', '/cobranca/boleto/v1/boletos',
                  lambda **kw: resposta_cadastro(kw['json']['nossoNumero']))
    response = http.post('/api/v1/sicredi/carne/lote', json={'billing_ids': ids})
    assert response.status_code == 200, response.text
    lote = db.get(SicrediLote, response.json()['id'])
    if legacy_order:
        lote.billing_ids = sorted(ids)  # Carnês já gravados pela versão anterior.
        db.commit()
    else:
        assert lote.billing_ids == ids
    before = len(sicredi.chamadas)
    numbers = {r.billing_id: r.nosso_numero for r in db.query(SicrediBoleto).all()}
    stored_ids = list(lote.billing_ids)
    progress = http.get(f'/api/v1/sicredi/lotes/{lote.ticket}').json()['parcelas']
    assert [p['billing_id'] for p in progress] == ids
    assert [p['numero_parcela'] for p in progress] == list(range(1, 13))
    details = http.get(f'/api/v1/boletos/carne?payer_client_id={cliente.id}').json()[0]['parcelas_detalhe']
    assert [p['billing_id'] for p in details] == ids
    assert [p['numero_parcela'] for p in details] == list(range(1, 13))

    printed = []
    draw = boleto_pdf._draw_carne_parcela

    def capture(canvas, data, y, number, total):
        printed.append((data.billing_id, data.data_vencimento, data.valor, number, total))
        return draw(canvas, data, y, number, total)

    monkeypatch.setattr(boleto_pdf, '_draw_carne_parcela', capture)
    pdf = http.get(f'/api/v1/boletos/carne/{lote.id}/pdf?banco=sicredi')
    assert pdf.status_code == 200, pdf.text
    assert printed == [(b.id, b.due_date, b.amount, i, 12) for i, b in enumerate(titles, 1)]
    pages = PdfReader(BytesIO(pdf.content)).pages
    assert len(pages) == 4 and [p.extract_text().count('748-X') for p in pages] == [3] * 4
    assert '10/12/2026' in pages[0].extract_text()
    assert '10/11/2027' in pages[-1].extract_text()
    repeated = http.post('/api/v1/sicredi/carne/lote', json={'billing_ids': sorted(ids)})
    assert repeated.status_code == 200 and repeated.json()['id'] == lote.id
    assert len(sicredi.chamadas) == before
    assert {r.billing_id: r.nosso_numero for r in db.query(SicrediBoleto).all()} == numbers
    assert db.query(SicrediLote).count() == 1
    db.refresh(lote)
    assert lote.billing_ids == stored_ids  # Consultar/baixar não altera títulos antigos.


def test_two_vehicles_and_service_consolidate_each_month_not_the_whole_booklet(http, db, cliente, plan, monthly):
    second = Contract(client_id=cliente.id, plan_id=plan.id, start_date=date(2026, 1, 1),
                      status='ativo', billing_day=20)
    service = ClientChargeItem(client_id=cliente.id, title='Servico avulso', quantity=1,
                               unit_price=Decimal('10'), total_amount=Decimal('10'), installment_count=1,
                               start_date=date(2026, 11, 1), active=True)
    db.add_all([second, service])
    db.commit()
    response = http.post(PREFIX + '/generate', params=PARAMS)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['payment_titles_generated'] == 12
    assert result['grand_total'] == 1569.76
    titles = list(db.scalars(select(Billing).where(Billing.id.in_(result['payment_billing_ids']))
                            .order_by(Billing.due_date)))
    assert titles[0].amount == Decimal('139.98')
    assert all(title.amount == Decimal('129.98') for title in titles[1:])
    assert [title.due_date for title in titles] == [add_months(date(2026, 12, 10), i) for i in range(12)]
    assert [title.period_label for title in titles] == [add_months(date(2026, 11, 1), i).strftime('%m/%Y') for i in range(12)]
    assert all(title.billing_type == 'boleto_unico' for title in titles)
    assert db.query(Billing).filter(Billing.billing_type == 'carne', Billing.substituted_by_id.is_not(None)).count() == 24


def test_existing_paid_future_month_is_preserved(http, db, monthly, cliente):
    paid = Billing(contract_id=monthly.id, client_id=cliente.id, amount=Decimal('64.99'),
                   billing_type='carne', due_date=date(2027, 2, 10), period_label='02/2027',
                   status=BillingStatus.PAID, payment_date=date(2026, 11, 11), paid_amount=Decimal('64.99'))
    db.add(paid)
    db.commit()
    preview = http.get(PREFIX + '/simulate', params=PARAMS).json()
    assert preview['already_generated'] == 1 and preview['to_generate'] == 11
    result = http.post(PREFIX + '/generate', params=PARAMS)
    assert result.status_code == 200, result.text
    assert result.json()['payment_titles_generated'] == 11
    db.refresh(paid)
    assert paid.status == BillingStatus.PAID and paid.paid_amount == Decimal('64.99')
    assert db.query(Billing).count() == 12


def test_released_archived_booklet_does_not_block_twelve_new_installments(http, db, monthly, cliente):
    for i in range(12):
        due = add_months(date(2026, 12, 10), i)
        db.add(Billing(contract_id=monthly.id, client_id=cliente.id, billing_type='carne',
                       amount=Decimal('64.99'), due_date=due, period_label=due.strftime('%m/%Y'),
                       status=BillingStatus.CANCELED, is_deleted=True, competencia_liberada=True))
    db.commit()
    result = http.post(PREFIX + '/generate', params=PARAMS)
    assert result.status_code == 200, result.text
    assert result.json()['payment_titles_generated'] == 12
    assert db.query(Billing).filter(Billing.is_deleted.is_(True)).count() == 12


def test_contract_end_and_month_end_dates_are_respected(http, db, monthly):
    monthly.billing_day = 31
    monthly.end_date = date(2027, 2, 28)
    db.commit()
    result = http.get(PREFIX + '/simulate', params=PARAMS)
    assert result.status_code == 200, result.text
    assert [row['due_date'] for row in result.json()['items']] == [
        '2026-12-31', '2027-01-31', '2027-02-28', '2027-03-31']


def test_monthly_closing_stays_monthly_without_explicit_booklet_request(http, db, monthly):
    params = {key: value for key, value in PARAMS.items() if key != 'carne_installments'}
    assert http.get(PREFIX + '/simulate', params=params).json()['to_generate'] == 1
    response = http.post(PREFIX + '/generate', params=params)
    assert response.status_code == 200, response.text
    assert response.json()['payment_titles_generated'] == 1


def test_initial_prorata_and_installation_service_are_not_repeated(http, db, monthly, cliente):
    monthly.start_date = date(2026, 11, 16)
    db.add(ClientChargeItem(client_id=cliente.id, contract_id=monthly.id, title='Instalacao',
                           quantity=1, unit_price=Decimal('10'), total_amount=Decimal('10'),
                           installment_count=1, start_date=date(2026, 11, 16), active=True))
    db.commit()
    preview = http.get(PREFIX + '/simulate', params=PARAMS).json()
    first, *rest = preview['items']
    assert first['is_prorata'] and first['billing_amount'] == 32.5
    assert first['total_first_billing'] == 42.5
    assert all(not item['is_prorata'] and not item['first_month_charges'] for item in rest)
    response = http.post(PREFIX + '/generate', params=PARAMS)
    assert response.status_code == 200, response.text
    result = response.json()
    titles = list(db.scalars(select(Billing).where(Billing.id.in_(result['payment_billing_ids']))
                            .order_by(Billing.due_date)))
    assert len(titles) == 12 and titles[0].amount == Decimal('42.50')
    assert all(title.amount == Decimal('64.99') for title in titles[1:])
    assert db.query(ClientChargeItem).one().status == 'faturado'


def test_future_month_is_revalidated_under_contract_lock(http, db, monthly, cliente, monkeypatch):
    from app.services import billing_closure
    lock = billing_closure._locked_contracts

    def concurrent_billing(session, ids):
        session.add(Billing(contract_id=monthly.id, client_id=cliente.id, amount=Decimal('64.99'),
                            due_date=date(2027, 1, 10), period_label='01/2027', billing_type='recorrente',
                            status=BillingStatus.PENDING))
        session.flush()
        return lock(session, ids)

    monkeypatch.setattr(billing_closure, '_locked_contracts', concurrent_billing)
    response = http.post(PREFIX + '/generate', params=PARAMS)
    assert response.status_code == 409, response.text
    assert 'Refaça a simulação' in response.json()['detail']
    assert db.query(Billing).count() == 0
    assert db.query(ClosureJob).count() == 0


@pytest.mark.parametrize('extra,code', [
    ({'carne_installments': 1}, 422), ({'carne_installments': 61}, 422),
    ({'forma_cobranca': 'boleto_mensal'}, 409),
])
def test_booklet_request_is_explicit_and_bounded(http, monthly, extra, code):
    assert http.get(PREFIX + '/simulate', params={**PARAMS, **extra}).status_code == code


def test_xlsx_uses_the_same_twelve_due_dates_and_service_periods(http, monthly):
    response = http.get(PREFIX + '/simulate/xlsx', params=PARAMS)
    assert response.status_code == 200, response.text
    workbook = load_workbook(BytesIO(response.content))
    sheet = workbook['Contratos']
    assert sheet.max_row == 13
    assert sheet.cell(2, 12).value.date() == date(2026, 12, 10)
    assert sheet.cell(13, 12).value.date() == date(2027, 11, 10)
    assert sheet.cell(2, 13).value == '11/2026'
    assert sheet.cell(13, 13).value == '10/2027'
