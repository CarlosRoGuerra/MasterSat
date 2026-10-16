import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.billing_change_log import BillingChangeLog
from app.models.cnab_remessa import CnabRemessa, CnabRemessaItem
from app.models.enums import BillingStatus
from tests.fase03_apoio import cobranca, registrar_titulo, reserva

URL = '/api/v1/billings/lote/acoes'


def preview(http, ids, action='cancelar'):
    result = http.post(URL, json={'billing_ids': ids, 'action': action})
    assert result.status_code == 200, result.text
    assert result.json()['simulacao'] is True
    return result.json()


def apply(http, review, **extra):
    return http.post(URL, json={'billing_ids': [item['billing_id'] for item in review['itens']],
        'action': review['action'], 'simular': False, 'revisao': review['revisao'],
        'reason': 'Solicitação do cliente', **extra})


def test_preview_is_read_only_and_reports_each_blocked_item(http, db, contrato):
    pending = cobranca(db, contrato)
    paid = cobranca(db, contrato, status=BillingStatus.PAID)
    canceled = cobranca(db, contrato, status=BillingStatus.CANCELED)
    result = preview(http, [pending.id, paid.id, canceled.id, 999999, pending.id])
    assert (result['total_enviados'], result['aplicados'], result['ignorados']) == (4, 1, 3)
    assert [item['situacao'] for item in result['itens']] == ['aplicavel', 'ignorado', 'ignorado', 'ignorado']
    db.refresh(pending)
    assert pending.status == BillingStatus.PENDING
    assert not db.scalars(select(BillingChangeLog)).all()
    assert not db.scalars(select(AuditLog)).all()


def test_cancel_applies_allowed_items_only_and_records_reason(http, db, contrato):
    pending = cobranca(db, contrato)
    overdue = cobranca(db, contrato, status=BillingStatus.OVERDUE)
    paid = cobranca(db, contrato, status=BillingStatus.PAID)
    before_paid = {c.name: getattr(paid, c.name) for c in paid.__table__.columns}
    review = preview(http, [pending.id, overdue.id, paid.id])
    result = apply(http, review)
    assert result.status_code == 200, result.text
    assert result.json()['simulacao'] is False
    assert result.json()['aplicados'] == 2
    assert result.json()['ignorados'] == 1
    for row in (pending, overdue, paid):
        db.refresh(row)
    assert pending.status == overdue.status == BillingStatus.CANCELED
    assert not pending.competencia_liberada
    assert before_paid == {c.name: getattr(paid, c.name) for c in paid.__table__.columns}
    assert 'Solicitação do cliente' in pending.notes
    assert len(db.scalars(select(BillingChangeLog)).all()) == 2
    assert len(db.scalars(select(AuditLog).where(AuditLog.path == '/billings/lote/acoes')).all()) == 2
    again = apply(http, review)
    assert again.status_code == 409
    assert again.json()['detail']['code'] == 'revisao_desatualizada'


def test_registered_cancel_requires_bank_ack_before_any_write(http, db, contrato):
    ordinary = cobranca(db, contrato)
    registered = cobranca(db, contrato)
    title = registrar_titulo(db, registered)
    review = preview(http, [ordinary.id, registered.id])
    assert review['itens'][1]['baixa_bancaria_pendente'] is True
    assert review['itens'][1]['nosso_numero'] == title.nosso_numero
    denied = apply(http, review)
    assert denied.status_code == 409
    assert denied.json()['detail']['code'] == 'confirmacao_bancaria_necessaria'
    db.refresh(ordinary)
    assert ordinary.status == BillingStatus.PENDING
    result = apply(http, review, confirmar_boleto_ailos=True)
    assert result.status_code == 200, result.text
    db.refresh(title)
    assert title.baixa_status == 'pendente'
    db.refresh(registered)
    assert 'baixa manual pendente' in registered.notes


def test_cnab_cancel_requires_ack_and_keeps_reservation_and_delete_protection(http, db, contrato):
    billing = cobranca(db, contrato)
    remessa = CnabRemessa(layout='240', sequencial=1, arquivo_sha256='a' * 64,
        arquivo=b'isolated-cnab-reservation', total_titulos=1, valor_total=billing.amount)
    db.add(remessa)
    db.flush()
    item = CnabRemessaItem(remessa_id=remessa.id, billing_id=billing.id,
        valor=billing.amount, vencimento=billing.due_date, nosso_numero='CNAB123')
    db.add(item)
    db.commit()
    review = preview(http, [billing.id])
    assert review['itens'][0]['baixa_bancaria_pendente'] is True
    assert review['itens'][0]['nosso_numero'] == 'CNAB123'
    assert apply(http, review).status_code == 409
    response = apply(http, review, confirmar_boleto_ailos=True)
    assert response.status_code == 200, response.text
    db.refresh(billing)
    db.refresh(item)
    assert billing.status == BillingStatus.CANCELED
    assert item.status == 'reservado'
    deletion = preview(http, [billing.id], 'excluir')
    assert deletion['aplicados'] == 0 and deletion['itens'][0]['motivo']
    assert apply(http, deletion).status_code == 200
    db.refresh(billing)
    assert not billing.is_deleted


@pytest.mark.parametrize('state', ['REGISTRANDO', 'DESFECHO_DESCONHECIDO'])
def test_in_flight_titles_are_skipped_while_safe_items_cancel(http, db, contrato, state):
    ordinary = cobranca(db, contrato)
    uncertain = cobranca(db, contrato)
    reserva(db, uncertain, state)
    review = preview(http, [ordinary.id, uncertain.id])
    assert review['aplicados'] == 1
    assert review['itens'][1]['situacao'] == 'ignorado'
    assert review['itens'][1]['motivo']
    response = apply(http, review)
    assert response.status_code == 200, response.text
    db.refresh(uncertain)
    assert uncertain.status == BillingStatus.PENDING


@pytest.mark.parametrize('change', ['paid', 'registered', 'amount'])
def test_review_detects_a_change_since_preview_before_any_mutation(http, db, contrato, change):
    first = cobranca(db, contrato)
    second = cobranca(db, contrato)
    review = preview(http, [first.id, second.id])
    if change == 'paid':
        second.status = BillingStatus.PAID
        db.commit()
    elif change == 'amount':
        second.amount = 123
        db.commit()
    else:
        registrar_titulo(db, second)
    result = apply(http, review, confirmar_boleto_ailos=True)
    assert result.status_code == 409
    assert result.json()['detail']['code'] == 'revisao_desatualizada'
    db.refresh(first)
    assert first.status == BillingStatus.PENDING
    assert not db.scalars(select(BillingChangeLog)).all()


def test_delete_only_canceled_and_preserves_paid_and_open(http, db, contrato):
    canceled = cobranca(db, contrato, status=BillingStatus.CANCELED)
    pending = cobranca(db, contrato)
    paid = cobranca(db, contrato, status=BillingStatus.PAID)
    review = preview(http, [canceled.id, pending.id, paid.id], 'excluir')
    assert (review['aplicados'], review['ignorados']) == (1, 2)
    response = apply(http, review)
    assert response.status_code == 200, response.text
    for row in (canceled, pending, paid):
        db.refresh(row)
    assert canceled.is_deleted and 'Excluída em lote' in canceled.notes
    assert not pending.is_deleted and not paid.is_deleted
    assert db.scalars(select(BillingChangeLog)).one().field_name == 'is_deleted'


@pytest.mark.parametrize('bank_state', ['registered', 'lowered', 'in_flight', 'unknown'])
def test_delete_keeps_every_item_with_bank_history(http, db, contrato, bank_state):
    billing = cobranca(db, contrato, status=BillingStatus.CANCELED)
    if bank_state in ('registered', 'lowered'):
        registrar_titulo(db, billing, **({'baixa_status': 'confirmada'} if bank_state == 'lowered' else {}))
    else:
        reserva(db, billing, 'REGISTRANDO' if bank_state == 'in_flight' else 'DESFECHO_DESCONHECIDO')
    review = preview(http, [billing.id], 'excluir')
    assert review['aplicados'] == 0 and review['itens'][0]['motivo']
    response = apply(http, review)
    assert response.status_code == 200, response.text
    db.refresh(billing)
    assert not billing.is_deleted


def test_delete_monthly_requires_explicit_release_ack(http, db, contrato):
    monthly = cobranca(db, contrato, billing_type='recorrente', period_label='11/2099', status=BillingStatus.CANCELED)
    review = preview(http, [monthly.id], 'excluir')
    assert review['itens'][0]['libera_competencia'] is True
    result = apply(http, review)
    assert result.status_code == 409
    assert result.json()['detail']['code'] == 'confirmacao_competencia_necessaria'
    db.refresh(monthly)
    assert not monthly.is_deleted
    result = apply(http, review, confirmar_liberacao_competencia=True)
    assert result.status_code == 200, result.text
    db.refresh(monthly)
    assert monthly.is_deleted


def test_negotiations_are_not_reverted_implicitly_even_in_a_mixed_batch(http, db, contrato):
    ordinary = cobranca(db, contrato)
    master = cobranca(db, contrato, billing_type='boleto_unico')
    child = cobranca(db, contrato, status=BillingStatus.CANCELED, substituted_by_id=master.id)
    review = preview(http, [ordinary.id, master.id])
    assert review['aplicados'] == 1
    assert 'individual' in review['itens'][1]['motivo']
    result = apply(http, review)
    assert result.status_code == 200, result.text
    for row in (master, child):
        db.refresh(row)
    assert master.status == BillingStatus.PENDING
    assert child.status == BillingStatus.CANCELED and child.substituted_by_id == master.id
    deleted = preview(http, [child.id], 'excluir')
    assert deleted['aplicados'] == 0


def test_unexpected_write_failure_rolls_back_all_items(http, db, contrato, monkeypatch):
    import app.api.v1.endpoints.billings as endpoint
    first = cobranca(db, contrato, status=BillingStatus.CANCELED)
    second = cobranca(db, contrato, status=BillingStatus.CANCELED)
    review = preview(http, [first.id, second.id], 'excluir')
    original = endpoint._delete_billing_locked
    def fail_on_second(billing, *args, **kwargs):
        if billing.id == second.id:
            raise RuntimeError('isolated failure')
        return original(billing, *args, **kwargs)
    monkeypatch.setattr(endpoint, '_delete_billing_locked', fail_on_second)
    response = apply(http, review)
    assert response.status_code == 500
    for row in (first, second):
        db.refresh(row)
        assert not row.is_deleted
    assert not db.scalars(select(BillingChangeLog)).all()
    assert not db.scalars(select(AuditLog).where(AuditLog.path == '/billings/lote/acoes')).all()


@pytest.mark.parametrize('fixture,status', [('http_fin', 200), ('http_op', 403), ('http_cliente', 403), ('http_unauth', 401)])
def test_financial_permissions(request, fixture, status, billing_pendente):
    response = request.getfixturevalue(fixture).post(URL, json={'billing_ids': [billing_pendente.id], 'action': 'cancelar'})
    assert response.status_code == status


@pytest.mark.parametrize('body', [
    {'billing_ids': [], 'action': 'cancelar'},
    {'billing_ids': [0], 'action': 'cancelar'},
    {'billing_ids': [1] * 1001, 'action': 'cancelar'},
    {'billing_ids': [1], 'action': 'pagar'},
    {'billing_ids': [1], 'action': 'cancelar', 'simular': False, 'reason': 'Justificativa'},
    {'billing_ids': [1], 'action': 'cancelar', 'simular': False, 'revisao': 'x', 'reason': '  '},
])
def test_payload_validation(http, body):
    assert http.post(URL, json=body).status_code == 422
