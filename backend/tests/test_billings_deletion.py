"""Exclusão lógica após baixa bancária; banco isolado, sem chamadas aos bancos."""
import pytest
from sqlalchemy import select

from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.billing_change_log import BillingChangeLog
from app.models.enums import BillingStatus
from app.models.sicredi_boleto import SicrediBoleto
from app.services.financial import existing_recurring_periods
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
@pytest.mark.parametrize('batch', [False, True])
def test_deletion_preserves_financial_discrepancy(http, db, contrato, banco, batch):
    billing = cobranca(db, contrato, status=BillingStatus.CANCELED)
    boleto = titulo(db, billing, banco, baixa_status='confirmada', pendencia='pago_em_cobranca_cancelada')
    response = apply(http, preview(http, [billing.id], 'excluir')) if batch else http.delete(f'{B}/{billing.id}')
    assert response.status_code == 200, response.text
    db.refresh(billing)
    db.refresh(boleto)
    assert billing.is_deleted and boleto.pendencia == 'pago_em_cobranca_cancelada'


def test_mixed_batch_deletes_all_canceled_and_preserves_paid(http, db, contrato):
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
    assert (review['aplicados'], review['ignorados']) == (3, 1)
    assert review['itens'][2]['baixa_bancaria_pendente'] is True
    response = apply(http, review, confirmar_boleto_ailos=True)
    assert response.status_code == 200, response.text
    for billing in (lowered_ailos, lowered_sicredi, active, paid):
        db.refresh(billing)
    assert lowered_ailos.is_deleted and lowered_sicredi.is_deleted
    assert active.is_deleted and not paid.is_deleted
    assert db.query(AilosBoleto).count() == db.query(SicrediBoleto).count() == 2
    assert len(db.scalars(select(BillingChangeLog).where(BillingChangeLog.field_name == 'is_deleted')).all()) == 3


def test_manual_lowering_confirmation_unlocks_deletion_and_keeps_audit(http, db, contrato):
    billing = cobranca(db, contrato, status=BillingStatus.CANCELED)
    titulo(db, billing, 'ailos', baixa_status='pendente')
    assert preview(http, [billing.id], 'excluir')['aplicados'] == 1
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


def test_deletion_keeps_both_banks_when_one_title_is_still_active(http, db, contrato):
    billing = cobranca(db, contrato, status=BillingStatus.CANCELED)
    titulo(db, billing, 'ailos', baixa_status='confirmada')
    active = titulo(db, billing, 'sicredi')
    assert http.delete(f'{B}/{billing.id}').status_code == 200
    db.refresh(active)
    assert active.baixa_status == 'pendente'


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


@pytest.mark.parametrize('banco', ['ailos', 'sicredi'])
def test_deleted_active_title_releases_month_and_keeps_bank_lowering_pending(http, db, contrato, banco):
    billing = cobranca(db, contrato, status=BillingStatus.CANCELED,
        billing_type='carne', period_label='11/2099')
    boleto = titulo(db, billing, banco)
    review = preview(http, [billing.id], 'excluir')
    assert review['aplicados'] == 1 and review['itens'][0]['libera_competencia']
    assert apply(http, review).status_code == 409  # exige ciência da baixa na origem
    assert apply(http, review, confirmar_boleto_ailos=True).status_code == 409  # e da liberação do mês
    response = apply(http, review, confirmar_boleto_ailos=True, confirmar_liberacao_competencia=True)
    assert response.status_code == 200, response.text
    db.refresh(billing)
    db.refresh(boleto)
    assert billing.is_deleted and billing.competencia_liberada
    assert boleto.baixa_status == 'pendente' and boleto.baixa_confirmada_em is None
    assert existing_recurring_periods(db, contrato.id, ['11/2099']) == set()
    assert http.post(f'{B}/', json={'client_id': contrato.client_id, 'contract_id': contrato.id,
        'billing_type': 'recorrente', 'period_label': '11/2099', 'due_date': '2099-12-10', 'amount': 100}).status_code == 200


def test_deleted_original_keeps_month_and_can_be_restored_by_explicit_reversal(http, db, contrato):
    master = cobranca(db, contrato, billing_type='boleto_unico')
    child = cobranca(db, contrato, status=BillingStatus.CANCELED, billing_type='recorrente',
        period_label='11/2099', substituted_by_id=master.id)
    assert http.delete(f'{B}/{child.id}').status_code == 200
    db.refresh(child)
    assert child.is_deleted and not child.competencia_liberada
    assert existing_recurring_periods(db, contrato.id, ['11/2099']) == {'11/2099'}
    result = http.post(f'{B}/{master.id}/cancel', json={'reason': 'Reverter negociação', 'reverter_substituicao': True})
    assert result.status_code == 200, result.text
    db.refresh(child)
    assert not child.is_deleted and child.status == BillingStatus.PENDING and child.substituted_by_id is None


def test_unknown_registration_resolved_after_deletion_stays_monitored(http, db, contrato):
    from app.services.ailos_boletos import _filtro_monitorados
    from tests.fase03_apoio import reserva
    billing = cobranca(db, contrato, status=BillingStatus.CANCELED)
    boleto = reserva(db, billing, 'DESFECHO_DESCONHECIDO')
    assert http.delete(f'{B}/{billing.id}').status_code == 200
    # Resposta tardia do registro não remove o título do rodízio de consultas.
    boleto.status_ailos = '0'
    boleto.nosso_numero = 'NN123'
    boleto.linha_digitavel = 'LD123'
    boleto.codigo_barras = 'CB123'
    db.commit()
    assert db.scalar(select(AilosBoleto.id).join(Billing, Billing.id == AilosBoleto.billing_id)
        .where(AilosBoleto.id == boleto.id, _filtro_monitorados())) == boleto.id
