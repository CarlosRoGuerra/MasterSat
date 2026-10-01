"""Fase 03 — FIN-06: nenhum centavo desaparece sem ajuste explícito.

Invariante de cobrança paga (ajustes ativos):
    paid_amount = amount - desconto - saldo_transferido + encargos + credito
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.billing import Billing
from app.models.billing_adjustment import BillingAdjustment
from app.models.billing_charge_item import BillingChargeItem
from app.models.client_charge_item import ClientChargeItem
from app.models.enums import BillingStatus
from app.models.nfse_nota import NfseNota
from tests.fase03_apoio import cobranca, registrar_titulo

B = '/api/v1/billings'


def _receber(http, billing_id, valor, **extra):
    corpo = {'paid_amount': valor, 'payment_date': '2099-11-05', 'payment_method': 'pix', **extra}
    return http.post(f'{B}/{billing_id}/receive', json=corpo)


def _invariante(db, billing: Billing) -> None:
    db.refresh(billing)
    ativos = db.query(BillingAdjustment).filter(
        BillingAdjustment.billing_id == billing.id,
        BillingAdjustment.reversed_at.is_(None),
        BillingAdjustment.kind != 'estorno',
    ).all()
    soma = {k: sum((Decimal(str(a.amount)) for a in ativos if a.kind == k), Decimal('0'))
            for k in ('desconto', 'saldo_transferido', 'encargos', 'credito')}
    esperado = (Decimal(str(billing.amount)) - soma['desconto'] - soma['saldo_transferido']
                + soma['encargos'] + soma['credito'])
    assert Decimal(str(billing.paid_amount)) == esperado


class TestRecebimentoDivergente:
    def test_valor_menor_sem_tratamento_e_recusado(self, http, db, contrato):
        b = cobranca(db, contrato)
        r = _receber(http, b.id, 1)
        assert r.status_code == 409
        d = r.json()['detail']
        assert d['code'] == 'recebimento_divergente' and d['diferenca'] == -99.0
        assert d['tratamentos'] == ['desconto', 'parcial']
        db.refresh(b)
        assert b.status == BillingStatus.PENDING and b.paid_amount is None

    def test_desconto_exige_justificativa_e_fica_registrado(self, http, db, contrato):
        b = cobranca(db, contrato)
        assert _receber(http, b.id, 90, tratamento_diferenca='desconto').status_code == 422
        r = _receber(http, b.id, 90, tratamento_diferenca='desconto', justificativa_diferenca='acordo comercial')
        assert r.status_code == 200 and r.json()['status'] == 'paga'
        ajuste = db.query(BillingAdjustment).filter_by(billing_id=b.id).one()
        assert (ajuste.kind, ajuste.amount, ajuste.created_by_user_id) == ('desconto', Decimal('10.00'), 1)
        _invariante(db, b)

    def test_parcial_quita_esta_e_cria_cobranca_do_saldo(self, http, db, contrato):
        b = cobranca(db, contrato)
        sem_venc = _receber(http, b.id, 1, tratamento_diferenca='parcial', justificativa_diferenca='pagou 1')
        assert sem_venc.status_code == 422
        r = _receber(http, b.id, 1, tratamento_diferenca='parcial', justificativa_diferenca='pagou 1',
                     saldo_vencimento='2099-12-10')
        assert r.status_code == 200
        ajuste = db.query(BillingAdjustment).filter_by(billing_id=b.id).one()
        saldo = db.get(Billing, ajuste.target_billing_id)
        assert ajuste.kind == 'saldo_transferido' and ajuste.amount == Decimal('99.00')
        assert saldo.amount == Decimal('99.00') and saldo.status == BillingStatus.PENDING
        assert saldo.billing_type == 'avulsa' and saldo.client_id == b.client_id
        assert saldo.payer_client_id == (b.payer_client_id or b.client_id)
        _invariante(db, b)
        # conservação: pago + saldo em aberto = título original
        assert Decimal(str(b.paid_amount)) + Decimal(str(saldo.amount)) == Decimal('100.00')

    def test_parcial_recusado_com_servico_vinculado(self, http, db, contrato, cliente):
        item = ClientChargeItem(client_id=cliente.id, contract_id=contrato.id, title='Instalação',
                                unit_price=Decimal('100.00'), total_amount=Decimal('100.00'),
                                installment_count=1, start_date=contrato.start_date)
        db.add(item)
        db.commit()
        b = cobranca(db, contrato)
        db.add(BillingChargeItem(billing_id=b.id, item_id=item.id, amount=Decimal('100.00')))
        db.commit()
        r = _receber(http, b.id, 50, tratamento_diferenca='parcial', justificativa_diferenca='x',
                     saldo_vencimento='2099-12-10')
        assert r.status_code == 409 and r.json()['detail']['code'] == 'parcial_indisponivel'

    def test_valor_maior_encargos_ou_credito(self, http, db, contrato):
        a = cobranca(db, contrato)
        assert _receber(http, a.id, 103).json()['detail']['tratamentos'] == ['encargos', 'credito']
        assert _receber(http, a.id, 103, tratamento_diferenca='encargos').status_code == 200
        assert db.query(BillingAdjustment).filter_by(billing_id=a.id).one().kind == 'encargos'
        _invariante(db, a)

        b = cobranca(db, contrato)
        assert _receber(http, b.id, 150, tratamento_diferenca='credito').status_code == 422
        assert _receber(http, b.id, 150, tratamento_diferenca='credito',
                        justificativa_diferenca='pagou duas parcelas').status_code == 200
        _invariante(db, b)

    @pytest.mark.parametrize('valor', [0, -1])
    def test_zero_ou_negativo_recusado(self, http, db, contrato, valor):
        b = cobranca(db, contrato)
        assert _receber(http, b.id, valor).status_code == 422

    def test_valor_igual_segue_como_antes(self, http, db, contrato):
        b = cobranca(db, contrato, amount=Decimal('0.30'))
        r = _receber(http, b.id, 0.1 + 0.2)  # 0.30000000000000004 em float
        assert r.status_code == 200
        assert db.query(BillingAdjustment).count() == 0
        db.refresh(b)
        assert b.paid_amount == Decimal('0.30')

    def test_receber_de_novo_e_recusado(self, http, db, contrato):
        b = cobranca(db, contrato)
        assert _receber(http, b.id, 100).status_code == 200
        assert _receber(http, b.id, 100).status_code == 400

    def test_notas_nao_sao_sobrescritas(self, http, db, contrato):
        b = cobranca(db, contrato, notes='Negociação: unifica #1, #2')
        _receber(http, b.id, 100, notes='pago no balcão')
        db.refresh(b)
        assert b.notes == 'Negociação: unifica #1, #2 | pago no balcão'


class TestEstorno:
    def test_estorno_reabre_e_preserva_o_pagamento_desfeito(self, http, db, contrato):
        b = cobranca(db, contrato)
        _receber(http, b.id, 90, tratamento_diferenca='desconto', justificativa_diferenca='acordo')
        db.refresh(b)
        recibo = b.receipt_number
        r = http.post(f'{B}/{b.id}/estornar', json={'justificativa': 'lançado na cobrança errada'})
        assert r.status_code == 200 and r.json()['status'] == 'pendente'
        db.refresh(b)
        assert b.paid_amount is None and b.receipt_number is None
        ajustes = http.get(f'{B}/{b.id}/ajustes').json()
        estorno = next(a for a in ajustes if a['kind'] == 'estorno')
        assert estorno['amount'] == 90.0 and estorno['details']['receipt_number'] == recibo
        desconto = next(a for a in ajustes if a['kind'] == 'desconto')
        assert desconto['reversed_at'] is not None

        # Receber de novo gera recibo novo e o invariante vale para o novo pagamento.
        assert _receber(http, b.id, 100).status_code == 200
        _invariante(db, b)

    def test_estorno_com_saldo_em_aberto_e_recusado(self, http, db, contrato):
        b = cobranca(db, contrato)
        _receber(http, b.id, 40, tratamento_diferenca='parcial', justificativa_diferenca='x',
                 saldo_vencimento='2099-12-10')
        r = http.post(f'{B}/{b.id}/estornar', json={'justificativa': 'erro'})
        assert r.status_code == 409 and r.json()['detail']['code'] == 'saldo_em_aberto'
        saldo_id = r.json()['detail']['billing_ids'][0]
        assert http.post(f'{B}/{saldo_id}/cancel', json={'reason': 'estorno do pagamento'}).status_code == 200
        assert http.post(f'{B}/{b.id}/estornar', json={'justificativa': 'erro'}).status_code == 200

    def test_pagamento_confirmado_pelo_banco_nao_e_estornado(self, http, db, contrato):
        b = cobranca(db, contrato, status=BillingStatus.PAID, paid_amount=Decimal('100.00'),
                     payment_method='boleto')
        registrar_titulo(db, b, payload_response={'boleto': {'indicadorSituacaoBoleto': 5,
                                                              'valorBoleto': {'valorPago': 100}}})
        r = http.post(f'{B}/{b.id}/estornar', json={'justificativa': 'erro'})
        assert r.status_code == 409 and r.json()['detail']['code'] == 'pagamento_bancario_confirmado'

    def test_nfse_emitida_bloqueia_estorno(self, http, db, contrato):
        b = cobranca(db, contrato, status=BillingStatus.PAID, paid_amount=Decimal('100.00'))
        db.add(NfseNota(billing_id=b.id, status='emitida'))
        db.commit()
        r = http.post(f'{B}/{b.id}/estornar', json={'justificativa': 'erro'})
        assert r.status_code == 409 and r.json()['detail']['code'] == 'nfse_vinculada'

    def test_estorno_de_cobranca_nao_paga(self, http, db, contrato):
        b = cobranca(db, contrato)
        assert http.post(f'{B}/{b.id}/estornar', json={'justificativa': 'erro'}).status_code == 409
