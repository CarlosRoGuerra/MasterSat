"""Fechamento de carnês Sicredi: o ano inteiro para quem precisa renovar."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.billing import Billing
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.services.financial import add_months

PREFIX = '/api/v1/billing-closure'


def _params(forma='carne_sicredi'):
    return {'service_month': '2026-11', 'forma_cobranca': forma, 'carne_installments': 12}


def _contrato(db, cliente, plan, forma='carne_sicredi'):
    cliente.forma_cobranca = forma
    cliente.boleto_format = 'unico'
    plan.price = Decimal('64.99')
    contrato = Contract(client_id=cliente.id, plan_id=plan.id, start_date=date(2026, 1, 1),
                        status='ativo', billing_day=10)
    db.add(contrato)
    db.commit()
    return contrato


def _parcelas_existentes(db, contrato, primeiro: date, quantidade: int):
    for i in range(quantidade):
        due = add_months(primeiro, i)
        db.add(Billing(contract_id=contrato.id, client_id=contrato.client_id, billing_type='carne',
                       amount=Decimal('64.99'), due_date=due, period_label=due.strftime('%m/%Y'),
                       installment_number=i + 1, installment_total=quantidade,
                       status=BillingStatus.PENDING))
    db.commit()


def test_carne_em_andamento_nao_ganha_pedaco_e_o_terminado_renova_o_ano(http, db, cliente, outro_cliente, plan):
    vigente = _contrato(db, cliente, plan)
    outro_cliente.forma_cobranca = 'carne_sicredi'
    terminado = Contract(client_id=outro_cliente.id, plan_id=plan.id, start_date=date(2026, 1, 1),
                         status='ativo', billing_day=10)
    db.add(terminado)
    db.commit()
    _parcelas_existentes(db, vigente, date(2026, 10, 10), 12)    # vai até 09/2027
    _parcelas_existentes(db, terminado, date(2025, 12, 10), 12)  # terminou em 11/2026

    preview = http.get(PREFIX + '/simulate', params=_params()).json()
    do_vigente = [i for i in preview['items'] if i['contract_id'] == vigente.id]
    do_terminado = [i for i in preview['items'] if i['contract_id'] == terminado.id]
    # Antes, o vigente ganhava 10 e 11/2027 soltos (um "carnê" de 2 parcelas).
    assert len(do_vigente) == 1 and do_vigente[0]['already_generated']
    assert len(do_terminado) == 12 and not any(i['already_generated'] for i in do_terminado)
    assert preview['to_generate'] == 12

    result = http.post(PREFIX + '/generate', params=_params())
    assert result.status_code == 200, result.text
    assert result.json()['payment_titles_generated'] == 12
    novos = list(db.scalars(select(Billing).where(Billing.id.in_(result.json()['payment_billing_ids']))
                            .order_by(Billing.due_date)))
    assert {b.contract_id for b in novos} == {terminado.id}
    assert [b.due_date for b in novos] == [add_months(date(2026, 12, 10), i) for i in range(12)]
    assert db.query(Billing).filter(Billing.contract_id == vigente.id).count() == 12

    path = f"{PREFIX}/lotes/{result.json()['closure_batch_id']}"
    grupo = http.get(path).json()['carnes'][0]
    assert grupo['banco'] == 'sicredi' and grupo['total'] == 12 and grupo['pode_iniciar']
    assert grupo['abertas'] == 12 and grupo['lote_status'] is None
    # Parcela paga sai do carnê impresso: o carnê conclui com as 11 em aberto.
    novos[0].status = BillingStatus.PAID
    db.commit()
    assert http.get(path).json()['carnes'][0]['abertas'] == 11


@pytest.mark.parametrize('forma', ['boleto_mensal', 'carne_ailos', 'carne_simples'])
def test_carne_do_fechamento_e_so_do_sicredi(http, db, cliente, plan, forma):
    _contrato(db, cliente, plan, forma)
    assert http.get(PREFIX + '/simulate', params=_params(forma)).status_code == 409
