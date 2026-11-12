"""Conferência do carnê antes de concluir: veículos uma vez, 12 parcelas reais."""
from datetime import date
from decimal import Decimal

from pypdf import PdfReader

from app.models.billing import Billing
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.services.billing_closure_carne_report import conference_groups
from app.services.billing_closure_report import generate_closure_pdf
from app.services.financial import add_months
from tests.test_closure_carne_sicredi import monthly, PARAMS, PREFIX  # noqa: F401


def pdf_text(buffer):
    return '\n'.join(page.extract_text() for page in PdfReader(buffer).pages)


def test_pdf_conference_shows_twelve_months_and_groups_vehicle_values_without_bank_calls(
    http, db, monthly, cliente, plan, monkeypatch,
):
    from app.services import sicredi_client, ailos_client

    def deny(*args, **kwargs):
        raise AssertionError('Conferir não chama o banco')

    monkeypatch.setattr(sicredi_client, 'request', deny)
    monkeypatch.setattr(ailos_client, 'request', deny)
    db.add(Contract(client_id=cliente.id, plan_id=plan.id, start_date=date(2026, 1, 1), status='ativo', billing_day=20))
    db.add(ClientChargeItem(client_id=cliente.id, title='Serviço na renovação', quantity=1,
                            unit_price=Decimal('10'), total_amount=Decimal('10'), installment_count=1,
                            start_date=date(2026, 11, 1), active=True))
    db.commit()
    response = http.get(PREFIX + '/simulate/pdf', params=PARAMS)
    assert response.status_code == 200, response.text
    from io import BytesIO
    text = pdf_text(BytesIO(response.content))
    assert 'CONFERÊNCIA DE CARNÊS SICREDI' in text
    assert '2 contrato(s) | 12 parcelas mensais' in text
    assert '1/12' in text and '12/12' in text
    assert '10/12/2026' in text and '10/11/2027' in text
    assert '139,98' in text and '129,98' in text and '1.569,76' in text
    assert 'Serviço na renovação: R$ 10,00' in text
    assert 'TOTAL BOLETO' not in text
    assert 'não emite boletos' in text
    assert db.query(Billing).count() == 0


def example_item(payer=1, **extra):
    return {'contract_id': 10, 'client_id': payer, 'client_name': 'CLIENTE DO VEÍCULO',
            'payer_client_id': payer, 'payer_name': 'NOME REPETIDO', 'plan_name': 'Mensalidade',
            'vehicle_plate': 'ABC1D23', 'tracker_imei': '12345', 'plan_price': 64.99,
            'billing_amount': 64.99, 'total_first_billing': 64.99,
            'due_date': date(2026, 12, 10), 'already_generated': False,
            'service_period_label': '11/2026', **extra}


def test_same_name_does_not_merge_different_payers_and_blocked_cycles_do_not_count():
    simulation = {'carne_installments': 12, 'reference_month': '11/2026', 'items': [
        example_item(1), example_item(2, contract_id=20),
        example_item(3, contract_id=30, already_generated=True, carne_renewal_blocked=True),
    ]}
    groups = conference_groups(simulation)
    assert len(groups) == 2
    assert [group['payer_key'] for group in groups] == [1, 2]
    assert [group['total'] for group in groups] == [Decimal('64.99')] * 2


def test_installation_and_fees_are_only_in_first_month_of_conference():
    items = [example_item(due_date=add_months(date(2026, 12, 10), i),
                          service_period_label=add_months(date(2026, 11, 1), i).strftime('%m/%Y'),
                          first_month_charges=[{'title': 'Instalação', 'amount': 30}] if not i else [])
             for i in range(12)]
    simulation = {'items': items,
                  'charge_items': [{'payer_client_id': 1, 'title': 'Serviço', 'amount_to_generate': 10}],
                  'uninstall_events': [{'payer_client_id': 1, 'fee_amount': 20},
                                       {'payer_client_id': 1, 'fee_amount': 2, 'deferred': True}]}
    group, = conference_groups(simulation)
    assert len(group['contracts']) == 1 and len(group['schedule']) == 12
    assert group['schedule'][0]['amount'] == Decimal('124.99')
    assert all(row['extra'] == 0 for row in group['schedule'][1:])
    assert group['total'] == Decimal('839.88')


def test_no_eligible_client_pdf_explains_renewal_reason():
    simulation = {'carne_installments': 12, 'reference_month': '11/2026',
                  'items': [example_item(already_generated=True, carne_renewal_blocked=True)],
                  'carne_renewal_blocks': [{'payer_client_id': 1, 'payer_name': 'CLIENTE COM CARNÊ VIGENTE',
                                            'reason': 'Já existem mensalidades para 12/2026.'}]}
    text = pdf_text(generate_closure_pdf(simulation))
    assert 'Nenhum cliente precisa de um novo carnê' in text
    assert 'CLIENTE COM CARNÊ VIGENTE' in text and '12/2026' in text
    assert '1/12' not in text


def test_monthly_pdf_keeps_existing_conference_format():
    simulation = {'reference_month': '11/2026', 'items': [example_item()], 'carne_installments': None}
    text = pdf_text(generate_closure_pdf(simulation))
    assert 'INTERVENIENTE:' in text and 'TOTAL BOLETO' in text
    assert 'CONFERÊNCIA DE CARNÊS SICREDI' not in text
