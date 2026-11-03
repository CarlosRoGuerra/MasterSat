import csv
from datetime import date
from decimal import Decimal
from io import BytesIO, StringIO

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader

from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus, ClientStatus


PREFIX = '/api/v1/exports/billings-report'
PERIOD = {
    'situacao': 'paga', 'periodo_por': 'pagamento',
    'date_from': '2026-09-01', 'date_to': '2026-11-03',
}


def _payment(db, client, amount, paid_on, *, kind='recorrente', paid_amount=None,
             sgr=None, payer=None):
    billing = Billing(
        client_id=client.id, payer_client_id=payer.id if payer else None,
        title=f'Boleto SGR 99 - {client.id}' if sgr else kind,
        billing_type=kind, amount=Decimal(str(amount)), due_date=date(2026, 9, 15),
        payment_date=paid_on, status=BillingStatus.PAID,
        paid_amount=Decimal(str(paid_amount)) if paid_amount is not None else None,
        sgr_payload={'cod_boleto': sgr, 'nosso_numero': '99'} if sgr else None,
    )
    db.add(billing)
    db.commit()
    return billing


@pytest.fixture()
def mixed_payments(db, cliente, outro_cliente):
    cliente.name = 'Alfa Serviços'
    outro_cliente.name = 'Beta Comércio'
    db.commit()
    monthly = _payment(db, cliente, 100, date(2026, 9, 10), paid_amount=110)
    service = _payment(db, cliente, 30, date(2026, 9, 20), kind='avulsa')
    fee = _payment(db, cliente, 160, date(2026, 10, 10), kind='taxa_desinstalacao')
    other = _payment(db, outro_cliente, 50, date(2026, 10, 5))
    return monthly, service, fee, other


@pytest.mark.parametrize('fmt', ['csv', 'xlsx'])
def test_paid_downloads_keep_all_customer_charges_together(http, mixed_payments, fmt):
    monthly, service, fee, other = mixed_payments

    response = http.get(PREFIX, params={**PERIOD, 'fmt': fmt})

    assert response.status_code == 200, response.text
    if fmt == 'csv':
        records = list(csv.DictReader(StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
    else:
        sheet = load_workbook(BytesIO(response.content), data_only=True).active
        rows = list(sheet.iter_rows(values_only=True))
        records = [dict(zip(rows[0], row)) for row in rows[1:]]
    assert [row['Cliente'] for row in records] == ['Alfa Serviços'] * 3 + ['Beta Comércio']
    assert [row['Nº do Boleto'] for row in records] == [
        f'#{fee.id}', f'#{service.id}', f'#{monthly.id}', f'#{other.id}',
    ]
    assert sum(Decimal(str(row['Valor Pago'])) for row in records) == Decimal('350.00')


def test_paid_pdf_has_customer_subtotals_without_changing_financial_totals(http, mixed_payments):
    monthly, service, fee, other = mixed_payments

    response = http.get(PREFIX, params={**PERIOD, 'fmt': 'pdf'})

    assert response.status_code == 200, response.text
    text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
    assert 'Agrupado por cliente' in text
    lines = text.splitlines()
    assert lines.index(f'#{fee.id}') < lines.index(f'#{service.id}') < lines.index(f'#{monthly.id}')
    assert lines.index(f'#{monthly.id}') < lines.index('Total do cliente: Alfa Serviços')
    assert lines.index('Total do cliente: Alfa Serviços') < lines.index(f'#{other.id}')
    assert text.count('Total do cliente:') == 2
    assert 'R$ 290,00' in text and 'R$ 300,00' in text
    assert 'R$ 340,00' in text and 'R$ 350,00' in text
    assert '4 cobrança(s)' in text


def test_sgr_customer_subtotal_counts_each_registered_boleto_once(http, db, cliente, monkeypatch):
    import app.api.v1.endpoints.exports as endpoint

    for _ in range(3):
        _payment(db, cliente, '64.99', date(2026, 10, 1), sgr='legacy-99')
    _payment(db, cliente, 50, date(2026, 10, 2), kind='avulsa')
    captured = {}
    original = endpoint._billings_report_pdf

    def capture(lines, *args, **kwargs):
        captured['lines'] = lines
        return original(lines, *args, **kwargs)

    monkeypatch.setattr(endpoint, '_billings_report_pdf', capture)
    response = http.get(PREFIX, params={**PERIOD, 'fmt': 'pdf'})

    assert response.status_code == 200, response.text
    assert len(captured['lines']) == 2
    assert sum(Decimal(str(ln['valor_pago'])) for ln in captured['lines']) == Decimal('244.97')
    text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(response.content)).pages)
    assert text.count('Total do cliente:') == 1
    assert '2 boleto(s)' in text
    assert 'R$ 244,97' in text


def test_different_customers_with_same_name_and_sgr_number_are_not_merged(http, db, cliente):
    namesake = Client(name=cliente.name, cpf_cnpj='11222333000199', type='pj', status=ClientStatus.ACTIVE)
    db.add(namesake)
    db.commit()
    _payment(db, cliente, 11, date(2026, 10, 1), sgr='same-code')
    _payment(db, namesake, 22, date(2026, 10, 1), sgr='same-code')

    response = http.get(PREFIX, params={**PERIOD, 'fmt': 'pdf'})

    assert response.status_code == 200, response.text
    text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(response.content)).pages)
    assert text.count('Total do cliente:') == 2
    assert 'R$ 11,00' in text and 'R$ 22,00' in text and 'R$ 33,00' in text


def test_paid_report_groups_by_recorded_payer_and_keeps_payment_filters(
    http, db, cliente, outro_cliente,
):
    _payment(db, cliente, 100, date(2026, 10, 1), payer=outro_cliente)
    _payment(db, outro_cliente, 30, date(2026, 10, 2), kind='avulsa')
    _payment(db, cliente, 999, date(2026, 8, 1), payer=outro_cliente)
    _payment(db, cliente, 888, date(2026, 10, 3))

    response = http.get(PREFIX, params={**PERIOD, 'fmt': 'pdf', 'client_id': outro_cliente.id})

    assert response.status_code == 200, response.text
    text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(response.content)).pages)
    assert text.count('Total do cliente:') == 1
    assert 'R$ 130,00' in text
    assert 'R$ 999,00' not in text and 'R$ 888,00' not in text


def test_paid_report_repeats_headers_and_keeps_subtotal_with_last_boleto(http, db, cliente):
    payments = [_payment(db, cliente, 10, date(2026, 10, 1)) for _ in range(65)]

    response = http.get(PREFIX, params={**PERIOD, 'fmt': 'pdf'})

    assert response.status_code == 200, response.text
    pages = [p.extract_text() for p in PdfReader(BytesIO(response.content)).pages]
    assert len(pages) >= 2
    assert all('Nº do Boleto' in p for p in pages)
    subtotal_page = next(p for p in pages if 'Total do cliente:' in p)
    assert f'#{payments[-1].id}' in subtotal_page.splitlines()
    assert 'R$ 650,00' in subtotal_page


def test_other_report_situations_keep_existing_layout(http, db, cliente):
    _payment(db, cliente, 10, date(2026, 10, 1))

    response = http.get(PREFIX, params={**PERIOD, 'situacao': 'todas', 'fmt': 'pdf'})

    assert response.status_code == 200, response.text
    text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(response.content)).pages)
    assert 'Agrupado por cliente' not in text
    assert 'Total do cliente:' not in text


def test_empty_paid_report_keeps_valid_pdf(http):
    response = http.get(PREFIX, params={**PERIOD, 'fmt': 'pdf'})

    assert response.status_code == 200, response.text
    text = PdfReader(BytesIO(response.content)).pages[0].extract_text()
    assert '0 cliente(s)' in text and 'R$ 0,00' in text
