"""Cancelamento local libera os meses, mantendo o aviso e a situação bancária real."""
from datetime import date
import pytest

from app.models.billing_change_log import BillingChangeLog
from app.models.enums import BillingStatus
from app.services import sicredi_client, ailos_client
from app.services.financial import existing_recurring_periods
from tests.fase03_apoio import cobranca
from tests.test_billings_deletion import titulo
from tests.test_billings_batch_actions import apply, preview

B = '/api/v1/billings'


@pytest.fixture(autouse=True)
def no_bank_calls(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('Cancelamento local não deve chamar o banco')
    monkeypatch.setattr(sicredi_client, 'request', denied)
    monkeypatch.setattr(ailos_client, 'request', denied)


@pytest.mark.parametrize('bank', ['ailos', 'sicredi'])
@pytest.mark.parametrize('flow', ['individual', 'batch', 'legacy_batch'])
def test_cancel_releases_month_and_preserves_real_bank_state(http, db, contrato, bank, flow):
    billing = cobranca(db, contrato, billing_type='carne', period_label='11/2099')
    bank_title = titulo(db, billing, bank)
    original_number = bank_title.nosso_numero
    if flow == 'individual':
        warning = http.post(f'{B}/{billing.id}/cancel', json={'reason': 'Refazer carnê'})
        assert warning.status_code == 409 and bank.capitalize() in warning.json()['detail']['message']
        result = http.post(f'{B}/{billing.id}/cancel', json={
            'reason': 'Refazer carnê', 'confirmar_boleto_ailos': True})
    elif flow == 'batch':
        review = preview(http, [billing.id])
        assert review['itens'][0]['libera_competencia']
        assert apply(http, review).status_code == 409
        result = apply(http, review, confirmar_boleto_ailos=True)
    else:
        result = http.post(f'{B}/lote/situacao', json={
            'action': 'cancelar', 'billing_ids': [billing.id], 'reason': 'Refazer carnê'})
    assert result.status_code == 200, result.text
    db.refresh(billing)
    db.refresh(bank_title)
    assert billing.status == BillingStatus.CANCELED and billing.competencia_liberada
    assert existing_recurring_periods(db, contrato.id, ['11/2099']) == set()
    assert bank_title.nosso_numero == original_number and bank_title.baixa_status == 'pendente'
    assert bank_title.baixa_confirmada_em is None
    assert ('site do Sicredi' if bank == 'sicredi' else 'site da Ailos') in billing.notes
    assert db.query(BillingChangeLog).filter_by(billing_id=billing.id, field_name='competencia_liberada').count() == 1
    # Libera a preparação local de novas parcelas; nenhuma emissão bancária é feita.
    result = http.post(f'{B}/parcelar', json={'contract_id': contrato.id,
        'num_parcelas': 12, 'banco': 'sicredi', 'primeiro_vencimento': '2099-11-15'})
    assert result.status_code == 200, result.text
    assert len(result.json()) == 12


@pytest.mark.parametrize('bank', ['ailos', 'sicredi'])
def test_already_archived_booklet_can_release_all_months_without_confirming_bank_lowering(http, db, contrato, bank):
    from app.services.financial import add_months
    bills = [cobranca(db, contrato, billing_type='carne', period_label=add_months(date(2099, 1, 15), i).strftime('%m/%Y'),
                      status=BillingStatus.CANCELED, is_deleted=True) for i in range(12)]
    records = [titulo(db, bill, bank, baixa_status='pendente') for bill in bills]
    for bill in bills:
        url = f'{B}/{bill.id}/liberar-competencia'
        assert http.post(url, json={'justificativa': 'Refazer carnê cancelado'}).status_code == 409
        result = http.post(url, json={'justificativa': 'Refazer carnê cancelado', 'confirmar_baixa_origem': True})
        assert result.status_code == 200, result.text
        assert result.json()['competencia_liberada']
    assert existing_recurring_periods(db, contrato.id, [b.period_label for b in bills]) == set()
    for record in records:
        db.refresh(record)
        assert record.baixa_status == 'pendente' and record.baixa_confirmada_em is None


def _carne_antigo(db, contrato, bank, meses=12):
    """Parcelas canceladas e excluídas pela regra anterior: ocupam o mês."""
    from app.services.financial import add_months
    bills = [cobranca(db, contrato, billing_type='carne', status=BillingStatus.CANCELED, is_deleted=True,
                      period_label=add_months(date(2099, 11, 15), i).strftime('%m/%Y'))
             for i in range(meses)]
    return bills, [titulo(db, bill, bank, baixa_status='pendente') for bill in bills]


@pytest.mark.parametrize('bank', ['ailos', 'sicredi'])
def test_new_booklet_releases_months_held_by_old_canceled_and_deleted_booklet(http, db, contrato, bank):
    bills, records = _carne_antigo(db, contrato, bank)
    body = {'contract_id': contrato.id, 'num_parcelas': 12, 'banco': bank, 'primeiro_vencimento': '2099-11-15'}
    blocked = http.post(f'{B}/parcelar', json=body)
    assert blocked.status_code == 409
    detail = blocked.json()['detail']
    assert detail['code'] == 'competencias_canceladas'
    assert sorted(detail['billing_ids']) == sorted(b.id for b in bills)
    assert ('site do Sicredi' if bank == 'sicredi' else 'site da Ailos') in detail['message']
    assert existing_recurring_periods(db, contrato.id, ['11/2099']) == {'11/2099'}  # nada mudou sem confirmar

    created = http.post(f'{B}/parcelar', json={**body, 'liberar_canceladas': True})
    assert created.status_code == 200, created.text
    assert [b['period_label'] for b in created.json()] == [b.period_label for b in bills]
    for bill, record in zip(bills, records):
        db.refresh(bill)
        db.refresh(record)
        assert bill.competencia_liberada and bill.is_deleted
        assert record.baixa_status == 'pendente' and record.baixa_confirmada_em is None
    assert db.query(BillingChangeLog).filter_by(field_name='competencia_liberada').count() == 12


def test_open_billing_in_the_period_keeps_the_original_conflict(http, db, contrato):
    _carne_antigo(db, contrato, 'sicredi', meses=6)
    aberta = cobranca(db, contrato, billing_type='recorrente', period_label='05/2100')
    body = {'contract_id': contrato.id, 'num_parcelas': 12, 'banco': 'sicredi',
            'primeiro_vencimento': '2099-11-15', 'liberar_canceladas': True}
    blocked = http.post(f'{B}/parcelar', json=body)
    assert blocked.status_code == 409
    assert 'já tem cobrança de mensalidade lançada' in blocked.json()['detail']
    db.refresh(aberta)
    assert aberta.status == BillingStatus.PENDING
    assert db.query(BillingChangeLog).filter_by(field_name='competencia_liberada').count() == 0
