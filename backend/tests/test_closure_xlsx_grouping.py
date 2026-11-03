from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.models.billing import Billing
from app.models.client_charge_item import ClientChargeItem
from app.models.uninstall_event import UninstallEvent
from app.services.billing_closure_report import generate_closure_xlsx


def _records(sheet):
    rows = list(sheet.iter_rows(values_only=True))
    return [dict(zip(rows[0], row)) for row in rows[1:]]


def test_xlsx_groups_every_charge_and_reconciles_pending_totals():
    alpha = {'client_id': 1, 'client_name': 'Alfa', 'payer_client_id': 1, 'payer_name': 'Alfa'}
    beta = {'client_id': 2, 'client_name': 'Beta', 'payer_client_id': 2, 'payer_name': 'Beta'}
    namesake = {**alpha, 'client_id': 3, 'payer_client_id': 3}
    simulation = {
        'reference_month': '10/2026',
        'items': [
            {**alpha, 'contract_id': 11, 'billing_amount': 100,
             'first_month_charges': [{'title': 'Instalação', 'amount': 20}]},
            {**beta, 'contract_id': 21, 'billing_amount': 80, 'already_generated': True,
             'first_month_charges': [{'title': 'Acessório já faturado', 'amount': 5}]},
            {**alpha, 'contract_id': 12, 'billing_amount': 50, 'is_final_prorata': True},
        ],
        'uninstall_events': [
            {**alpha, 'event_id': 1, 'fee_amount': 160},
            {**beta, 'event_id': 2, 'fee_amount': 2, 'deferred': True},
        ],
        'charge_items': [
            {**beta, 'title': 'Serviço parcelado', 'amount_to_generate': 33.33,
             'installment_count': 3, 'generated_count': 1, 'total_remaining': 66.67},
            {**alpha, 'title': 'Visita técnica', 'amount_to_generate': 30},
            {**namesake, 'title': 'Avulsa sem mensalidade', 'amount_to_generate': 9.99},
            {**alpha, 'title': 'Adicional do rastreador', 'amount_to_generate': 10},
        ],
        'grand_total': 413.32,
    }

    workbook = load_workbook(generate_closure_xlsx(simulation), data_only=True)

    assert workbook.active.title == 'Conferência por cliente'
    records = _records(workbook.active)
    customers = {r['Cód. cliente']: r for r in records[:-1]}
    assert set(customers) == {1, 2, 3}  # Homônimos não são o mesmo cliente.
    assert customers[1]['Mensalidades'] == 150
    assert customers[1]['Produtos / 1ª cobrança'] == 20
    assert customers[1]['Taxas de desinstalação'] == 160
    assert customers[1]['Serviços avulsos'] == 40
    assert customers[1]['Total a gerar'] == 370
    assert customers[2]['Total a gerar'] == 33.33
    assert customers[2]['Já faturado'] == 85
    assert customers[2]['Aguardando acumulação'] == 2
    assert customers[3]['Total a gerar'] == 9.99
    assert records[-1]['Responsável financeiro'] == 'TOTAL GERAL'
    assert records[-1]['Total a gerar'] == simulation['grand_total']

    details = _records(workbook['Detalhamento por cliente'])
    assert [r['Cód. cliente'] for r in details] == [1] * 6 + [3] + [2] * 4
    assert {r['Tipo'] for r in details[:6]} == {
        'Mensalidade / pró-rata', 'Produto / 1ª cobrança', 'Desinstalação', 'Serviço avulso',
    }
    installment = next(r for r in details if r['Descrição'] == 'Serviço parcelado')
    assert installment['Parcela'] == '2/3'
    assert installment['A gerar'] == 33.33  # Apenas a parcela deste fechamento.
    for customer_id, customer in customers.items():
        customer_details = [r for r in details if r['Cód. cliente'] == customer_id]
        assert sum(Decimal(str(r['A gerar'])) for r in customer_details) == Decimal(str(customer['Total a gerar']))
    summary = dict(workbook['Resumo'].iter_rows(min_col=1, max_col=2, values_only=True))
    assert summary['TOTAL GERAL A GERAR'] == simulation['grand_total']


def test_xlsx_uses_the_same_financial_responsible_for_monthly_fees_and_services():
    owner = {'client_id': 1, 'client_name': 'Filial A', 'payer_client_id': 99, 'payer_name': 'Matriz'}
    other_owner = {**owner, 'client_id': 2, 'client_name': 'Filial B'}
    simulation = {
        'reference_month': '10/2026',
        'items': [
            {**owner, 'contract_id': 1, 'billing_amount': 100},
            {**other_owner, 'contract_id': 2, 'billing_amount': 50},
        ],
        'uninstall_events': [{**other_owner, 'fee_amount': 160}],
        'charge_items': [{**owner, 'title': 'Adicional', 'amount_to_generate': 30}],
    }

    workbook = load_workbook(generate_closure_xlsx(simulation), data_only=True)

    customers = _records(workbook.active)[:-1]
    assert len(customers) == 1
    assert customers[0]['Responsável financeiro'] == 'Matriz'
    assert customers[0]['Cód. cliente'] == 99
    assert customers[0]['Cliente(s)'] == 'Filial A; Filial B'
    assert customers[0]['Total a gerar'] == 340
    assert {r['Cód. cliente'] for r in _records(workbook['Detalhamento por cliente'])} == {99}


@pytest.mark.parametrize('services', [[], [{
    'client_id': 7, 'client_name': 'Só avulsas', 'title': 'Serviço', 'amount_to_generate': 25,
}]])
def test_xlsx_handles_empty_closure_and_customers_without_contracts(services):
    workbook = load_workbook(generate_closure_xlsx({'charge_items': services}), data_only=True)

    records = _records(workbook.active)
    assert records[-1]['Total a gerar'] == (25 if services else 0)
    assert len(records) == (2 if services else 1)


def test_excel_endpoint_groups_tracker_charge_monthly_and_uninstall_fee(
    http, db, cliente, contrato, veiculo,
):
    charge = ClientChargeItem(
        client_id=cliente.id, contract_id=contrato.id, vehicle_id=veiculo.id,
        tracker_id=contrato.tracker_id, title='Adicional do rastreador',
        quantity=1, unit_price=Decimal('30.00'), total_amount=Decimal('30.00'),
        installment_count=1, date_basis='due_date', start_date=date(2026, 11, 15),
        active=True,
    )
    event = UninstallEvent(
        client_id=cliente.id, vehicle_id=veiculo.id, uninstall_date=date(2026, 10, 9),
        fee_amount=Decimal('160.00'), status='pending',
    )
    db.add_all([charge, event])
    db.commit()

    response = http.get('/api/v1/billing-closure/simulate/xlsx', params={
        'service_month': '2026-10', 'filter_type': 'client', 'client_id': cliente.id,
    })

    assert response.status_code == 200, response.text
    workbook = load_workbook(BytesIO(response.content), data_only=True)
    customers = _records(workbook.active)[:-1]
    assert len(customers) == 1
    assert customers[0]['Mensalidades'] == 99.9
    assert customers[0]['Taxas de desinstalação'] == 160
    assert customers[0]['Serviços avulsos'] == 30
    assert customers[0]['Total a gerar'] == 289.9
    assert {r['Tipo'] for r in _records(workbook['Detalhamento por cliente'])} == {
        'Mensalidade / pró-rata', 'Desinstalação', 'Serviço avulso',
    }
    assert db.query(Billing).count() == 0
    db.refresh(event)
    assert event.status == 'pending'
    assert event.billing_id is None
