"""Exclusão lógica após baixa bancária; banco isolado, sem chamadas aos bancos."""
import pytest
from sqlalchemy import select

from app.models.ailos_boleto import AilosBoleto
from app.models.billing_change_log import BillingChangeLog
from app.models.enums import BillingStatus
from app.models.sicredi_boleto import SicrediBoleto
from tests.fase03_apoio import cobranca, registrar_titulo
from tests.test_billings_batch_actions import apply, preview

B = '/api/v1/billings'


def titulo(db, billing, banco, **extra):
    if banco == 'ailos':
        return registrar_titulo(db, billing, **extra)
    boleto = SicrediBoleto(
        billing_id=billing.id, ambiente='sandbox', cooperativa='1234', posto='01',
        codigo_beneficiario='12345', nosso_numero=f'{billing.id:09d}',
        status='REGISTRADO', linha_digitavel='LD123', codigo_barras='CB123', **extra,
    )
    db.add(boleto)
    db.commit()
    return boleto


@pytest.mark.parametrize('banco', ['ailos', 'sicredi'])
def test_delete_lowered_and_canceled_preserves_bank_record_and_history(http, db, contrato, banco):
    billing = cobranca(db, contrato, status=BillingStatus.CANCELED, notes='Motivo do cancelamento')
    boleto = titulo(db, billing, banco, baixa_status='confirmada')
    db.add(BillingChangeLog(billing_id=billing.id, field_name='baixa_bancaria',
        previous_value='pendente', new_value='confirmada', justification='Baixa conferida'))
    db.commit()

    response = http.delete(f'{B}/{billing.id}')
    assert response.status_code == 200, response.text
    db.refresh(billing)
    db.refresh(boleto)
    assert billing.is_deleted and billing.status == BillingStatus.CANCELED
    assert billing.notes == 'Motivo do cancelamento'
    assert boleto.baixa_status == 'confirmada' and boleto.nosso_numero
    assert db.scalars(select(BillingChangeLog).where(BillingChangeLog.billing_id == billing.id)).one()
    assert http.get(f'{B}/{billing.id}').status_code == 404


@pytest.mark.parametrize('banco', ['ailos', 'sicredi'])
@pytest.mark.parametrize('status', [BillingStatus.PENDING, BillingStatus.PAID])
def test_bank_lowering_does_not_allow_deleting_open_or_paid_billing(http, db, contrato, banco, status):
    billing = cobranca(db, contrato, status=status)
    titulo(db, billing, banco, baixa_status='confirmada')
    assert http.delete(f'{B}/{billing.id}').status_code == 400
    review = preview(http, [billing.id], 'excluir')
    assert review['aplicados'] == 0
    db.refresh(billing)
    assert not billing.is_deleted and billing.status == status


@pytest.mark.parametrize('banco', ['ailos', 'sicredi'])
def test_bank_discrepancy_requires_resolution_before_individual_or_batch_deletion(http, db, contrato, banco):
    billing = cobranca(db, contrato, status=BillingStatus.CANCELED)
    titulo(db, billing, banco, baixa_status='confirmada', pendencia='pago_em_cobranca_cancelada')
    response = http.delete(f'{B}/{billing.id}')
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'pendencia_bancaria_na_exclusao'
    review = preview(http, [billing.id], 'excluir')
    assert review['aplicados'] == 0 and 'pendência bancária' in review['itens'][0]['motivo']
    db.refresh(billing)
    assert not billing.is_deleted


def test_mixed_batch_deletes_lowered_titles_and_keeps_active_and_paid_items(http, db, contrato):
    lowered_ailos = cobranca(db, contrato, status=BillingStatus.CANCELED)
    lowered_sicredi = cobranca(db, contrato, status=BillingStatus.CANCELED)
    active = cobranca(db, contrato, status=BillingStatus.CANCELED)
    paid = cobranca(db, contrato, status=BillingStatus.PAID)
    titulo(db, lowered_ailos, 'ailos', baixa_status='confirmada')
    titulo(db, lowered_sicredi, 'sicredi', baixa_status='confirmada')
    titulo(db, active, 'ailos', baixa_status='pendente')
    titulo(db, paid, 'sicredi', baixa_status='confirmada')
    ids = [lowered_ailos.id, lowered_sicredi.id, active.id, paid.id]
    review = preview(http, ids, 'excluir')
    assert (review['aplicados'], review['ignorados']) == (2, 2)
    assert 'ainda não está confirmada' in review['itens'][2]['motivo']
    response = apply(http, review)
    assert response.status_code == 200, response.text
    for billing in (lowered_ailos, lowered_sicredi, active, paid):
        db.refresh(billing)
    assert lowered_ailos.is_deleted and lowered_sicredi.is_deleted
    assert not active.is_deleted and not paid.is_deleted
    assert db.query(AilosBoleto).count() == db.query(SicrediBoleto).count() == 2
    assert len(db.scalars(select(BillingChangeLog).where(BillingChangeLog.field_name == 'is_deleted')).all()) == 2


def test_manual_lowering_confirmation_unlocks_deletion_and_keeps_audit(http, db, contrato):
    billing = cobranca(db, contrato, status=BillingStatus.CANCELED)
    titulo(db, billing, 'ailos', baixa_status='pendente')
    assert preview(http, [billing.id], 'excluir')['aplicados'] == 0
    response = http.post(f'/api/v1/ailos/boletos/{billing.id}/confirmar-baixa',
        json={'justificativa': 'Conferido no internet banking: título cancelado'})
    assert response.status_code == 200, response.text
    review = preview(http, [billing.id], 'excluir')
    assert review['aplicados'] == 1
    assert apply(http, review).status_code == 200
    history = db.scalars(select(BillingChangeLog).where(BillingChangeLog.field_name == 'baixa_bancaria')).one()
    assert history.changed_by_user_id == 1 and history.new_value == 'confirmada'


def test_monthly_lowered_title_still_requires_confirmation_to_release_period(http, db, contrato):
    billing = cobranca(db, contrato, status=BillingStatus.CANCELED,
        billing_type='recorrente', period_label='11/2099')
    titulo(db, billing, 'ailos', baixa_status='confirmada')
    review = preview(http, [billing.id], 'excluir')
    assert review['itens'][0]['libera_competencia'] is True
    assert apply(http, review).status_code == 409
    assert apply(http, review, confirmar_liberacao_competencia=True).status_code == 200


def test_other_active_bank_title_still_blocks_deletion(http, db, contrato):
    billing = cobranca(db, contrato, status=BillingStatus.CANCELED)
    titulo(db, billing, 'ailos', baixa_status='confirmada')
    titulo(db, billing, 'sicredi')
    assert http.delete(f'{B}/{billing.id}').status_code == 409
    assert preview(http, [billing.id], 'excluir')['aplicados'] == 0


def test_bank_state_change_invalidates_review_before_any_deletion(http, db, contrato):
    billing = cobranca(db, contrato, status=BillingStatus.CANCELED)
    boleto = titulo(db, billing, 'ailos', baixa_status='confirmada')
    review = preview(http, [billing.id], 'excluir')
    boleto.baixa_status = 'pendente'
    db.commit()
    response = apply(http, review)
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'revisao_desatualizada'
    db.refresh(billing)
    assert not billing.is_deleted
