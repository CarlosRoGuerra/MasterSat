"""Serviço parcelado gera UMA parcela por fechamento (não todas de uma vez).

Caso real: "MENSALIDADE EM ABERTO" de R$ 288,57 em 3 parcelas de R$ 96,19 deve
entrar no boleto de outubro (96,19), novembro e dezembro — e não virar 3
cobranças de uma vez (548,53 no total do fechamento de outubro).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.billing import Billing
from app.models.client_charge_item import ClientChargeItem
from app.models.enums import BillingStatus
from app.services.billing_closure import execute_closure, simulate_closure
from app.services.financial import marcar_billing_pago

OUT = date(2026, 10, 1)
NOV = date(2026, 11, 1)
DEZ = date(2026, 12, 1)


@pytest.fixture()
def item(db, cliente):
    obj = ClientChargeItem(
        client_id=cliente.id, title='MENSALIDADE EM ABERTO', quantity=1,
        unit_price=Decimal('288.57'), total_amount=Decimal('288.57'),
        installment_count=3, start_date=date(2022, 3, 1), remove_after_payment=True,
    )
    db.add(obj)
    db.commit()
    return obj


def _parcelas(db, item):
    return db.query(Billing).filter(
        Billing.item_id == item.id, Billing.is_deleted.is_(False),
    ).order_by(Billing.installment_number).all()


def test_previa_promete_so_a_parcela_do_mes(db, item):
    previa = simulate_closure(db, OUT)
    assert previa['total_services'] == pytest.approx(96.19)
    servico = previa['charge_items'][0]
    assert servico['amount_to_generate'] == pytest.approx(96.19)
    assert servico['total_remaining'] == pytest.approx(288.57)
    assert servico['remaining_installments'] == 3


def test_fechamento_gera_so_a_parcela_do_mes_com_vencimento_no_mes(db, item):
    resultado = execute_closure(db, OUT)
    assert resultado['services_generated'] == 1
    assert resultado['total_services_amount'] == pytest.approx(96.19)
    parcelas = _parcelas(db, item)
    assert [(b.installment_number, b.due_date.month, b.due_date.year) for b in parcelas] == [(1, 10, 2026)]
    db.refresh(item)
    assert item.active is True  # faltam 2 parcelas: continua na fila


def test_rodar_o_mesmo_fechamento_de_novo_nao_adianta_parcela(db, item):
    execute_closure(db, OUT)
    assert simulate_closure(db, OUT)['charge_items'] == []
    resultado = execute_closure(db, OUT)
    assert resultado['services_generated'] == 0
    assert len(_parcelas(db, item)) == 1


def test_uma_parcela_por_mes_ate_acabar(db, item):
    for mes in (OUT, NOV, DEZ):
        assert execute_closure(db, mes)['services_generated'] == 1
    parcelas = _parcelas(db, item)
    assert [(b.installment_number, b.due_date.month) for b in parcelas] == [(1, 10), (2, 11), (3, 12)]
    assert sum(b.amount for b in parcelas) == Decimal('288.57')
    db.refresh(item)
    assert item.active is False and item.status == 'faturado'
    # Acabou: não aparece mais.
    assert simulate_closure(db, date(2027, 1, 1))['charge_items'] == []


def test_pagar_a_primeira_nao_encerra_o_item(db, item):
    execute_closure(db, OUT)
    primeira = _parcelas(db, item)[0]
    marcar_billing_pago(db, primeira, payment_date=date(2026, 10, 15), paid_amount=primeira.amount)
    db.refresh(item)
    assert item.status != 'concluido' and item.active is True
    # E a parcela 2 ainda sai no mês seguinte.
    assert execute_closure(db, NOV)['services_generated'] == 1


def test_item_so_conclui_quando_a_ultima_parcela_e_paga(db, item):
    for mes in (OUT, NOV, DEZ):
        execute_closure(db, mes)
    for parcela in _parcelas(db, item):
        marcar_billing_pago(db, parcela, payment_date=date(2026, 12, 20), paid_amount=parcela.amount)
    db.refresh(item)
    assert item.status == 'concluido' and item.active is False


def test_parcela_unica_continua_gerando_tudo_de_uma_vez(db, cliente):
    obj = ClientChargeItem(
        client_id=cliente.id, title='Taxa', quantity=1, unit_price=Decimal('100'),
        total_amount=Decimal('100'), installment_count=1, start_date=date(2026, 10, 10),
    )
    db.add(obj)
    db.commit()
    resultado = execute_closure(db, OUT)
    assert resultado['total_services_amount'] == pytest.approx(100.00)
    db.refresh(obj)
    assert obj.active is False and obj.status == 'faturado'
