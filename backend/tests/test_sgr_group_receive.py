"""Baixa integral do boleto SGR agrupado, com dados sintéticos."""
from datetime import date
from decimal import Decimal

import pytest

from app.models.enums import BillingStatus
from tests.fase03_apoio import cobranca, registrar_titulo, reserva

URL = '/api/v1/billings/lote/situacao'


def group(db, contrato, count=2):
    return [cobranca(db, contrato, amount=Decimal('64.99'),
                     status=BillingStatus.OVERDUE, due_date=date(2026, 9, 25),
                     sgr_payload={'cod_boleto': '19398'}, notes='Importado do SGR')
            for _ in range(count)]


def payload(items, **overrides):
    return {'action': 'receber', 'billing_ids': [b.id for b in items],
            'sgr_cod_boleto': '19398',
            'expected_amount': str(sum((b.amount for b in items), Decimal('0'))),
            'payment_date': '2026-10-23', 'payment_method': 'pix',
            'notes': 'PIX recebido direto', **overrides}


@pytest.mark.parametrize('profile', ['http', 'http_fin'])
def test_receives_full_group_with_exact_total_and_shared_receipt(request, profile, db, contrato):
    items = group(db, contrato, 12)
    response = request.getfixturevalue(profile).post(URL, json=payload(items))
    assert response.status_code == 200, response.text
    assert set(response.json()['processados']) == {b.id for b in items}
    assert response.json()['ignorados'] == []
    for b in items:
        db.refresh(b)
        assert b.status == BillingStatus.PAID
        assert b.paid_amount == Decimal('64.99')
        assert b.payment_date == date(2026, 10, 23)
        assert b.payment_method == 'pix'
        assert b.notes == 'Importado do SGR | PIX recebido direto'
    assert sum(b.paid_amount for b in items) == Decimal('779.88')
    assert len({b.receipt_number for b in items}) == 1
    assert items[0].receipt_number


@pytest.mark.parametrize('change', ['amount', 'paid', 'canceled', 'deleted', 'missing', 'other_group', 'incomplete', 'new_item', 'payer'])
def test_refuses_changed_composition_without_receiving_anything(http, db, contrato, outro_cliente, change):
    items = group(db, contrato)
    body = payload(items)
    if change == 'amount':
        items[1].amount = Decimal('70.00')
    elif change == 'paid':
        items[1].status = BillingStatus.PAID
        items[1].paid_amount = items[1].amount
    elif change == 'canceled':
        items[1].status = BillingStatus.CANCELED
    elif change == 'deleted':
        items[1].is_deleted = True
    elif change == 'missing':
        body['billing_ids'][1] = 999999
    elif change == 'other_group':
        items[1].sgr_payload = {'cod_boleto': 'OTHER'}
    elif change == 'incomplete':
        body = payload(items[:1])
    elif change == 'new_item':
        group(db, contrato, 1)
    elif change == 'payer':
        items[1].payer_client_id = outro_cliente.id
    db.commit()
    response = http.post(URL, json=body)
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'sgr_boleto_alterado'
    db.refresh(items[0])
    assert items[0].status == BillingStatus.OVERDUE
    assert items[0].paid_amount is None
    assert items[0].receipt_number is None
    assert items[0].notes == 'Importado do SGR'


def test_receives_remaining_open_items_and_prevents_duplicate(http, db, contrato):
    items = group(db, contrato, 3)
    items[2].status = BillingStatus.PAID
    items[2].paid_amount = items[2].amount
    db.commit()
    body = payload(items[:2])
    assert http.post(URL, json=body).status_code == 200
    assert http.post(URL, json=body).status_code == 409
    db.refresh(items[2])
    assert items[2].notes == 'Importado do SGR'


def test_preserves_bank_policy_for_entire_group(http, db, contrato):
    items = group(db, contrato)
    reserva(db, items[1], 'REGISTRANDO')
    response = http.post(URL, json=payload(items))
    assert response.status_code == 409, response.text
    db.refresh(items[0])
    assert items[0].status == BillingStatus.OVERDUE


def test_marks_active_bank_title_as_pending_cancellation(http, db, contrato):
    items = group(db, contrato)
    title = registrar_titulo(db, items[1])
    response = http.post(URL, json=payload(items))
    assert response.status_code == 200, response.text
    db.refresh(title)
    assert title.baixa_status == 'pendente'


@pytest.mark.parametrize('profile', ['http_op', 'http_cliente'])
def test_forbidden_profiles(request, profile, db, contrato):
    items = group(db, contrato)
    assert request.getfixturevalue(profile).post(URL, json=payload(items)).status_code == 403


def test_requires_expected_total(http, db, contrato):
    items = group(db, contrato)
    assert http.post(URL, json=payload(items, expected_amount=None)).status_code == 422
