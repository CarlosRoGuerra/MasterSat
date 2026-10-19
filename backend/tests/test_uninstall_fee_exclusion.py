import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import update

from app.models.audit_log import AuditLog
from app.models.billing import Billing
from app.models.enums import BillingStatus
from app.models.uninstall_event import UninstallEvent
from app.services.billing_closure import execute_closure, simulate_closure


@pytest.fixture()
def fee(db, cliente, veiculo):
    event = UninstallEvent(vehicle_id=veiculo.id, client_id=cliente.id,
        payer_client_id=cliente.id, uninstall_date=date(2026, 10, 16),
        fee_amount=Decimal('160.00'), status='pending', notes='Retirada registrada')
    db.add(event)
    db.commit()
    return event


def exclude(http, fee, reason='Taxa já paga pelo boleto simples'):
    return http.post(f'/api/v1/billing-closure/uninstall-events/{fee.id}/exclude', json={'reason': reason})


@pytest.mark.parametrize('state', ['pending', 'skipped'])
def test_exclusion_is_persistent_audited_and_preserves_paid_billing(http, db, fee, cliente, state):
    fee.status = state
    paid = Billing(client_id=cliente.id, amount=Decimal('160.00'), due_date=date(2026, 10, 16),
        status=BillingStatus.PAID, billing_type='avulsa', somente_sistema=True,
        receipt_number='RECIBO-PAGO', notes='Desinstalação já recebida')
    db.add(paid)
    db.commit()
    paid_before = {column.name: getattr(paid, column.name) for column in paid.__table__.columns}
    response = exclude(http, fee, '  Taxa já paga pelo boleto simples  ')
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'canceled'
    for row in (fee, paid):
        db.refresh(row)
    assert fee.status == 'canceled' and fee.billing_id is None
    assert fee.fee_amount == Decimal('160.00')
    assert 'Retirada registrada' in fee.notes and 'Taxa já paga pelo boleto simples' in fee.notes
    assert paid_before == {column.name: getattr(paid, column.name) for column in paid.__table__.columns}
    audit = db.query(AuditLog).filter_by(entity_type='uninstall_event', entity_id=fee.id).one()
    assert json.loads(audit.description)['reason'] == 'Taxa já paga pelo boleto simples'
    assert json.loads(audit.description)['previous_status'] == state
    for month in (date(2026, 10, 1), date(2026, 11, 1), date(2027, 1, 1)):
        preview = simulate_closure(db, month)
        assert preview['uninstall_events'] == [] and preview['total_uninstall_fees'] == 0
        result = execute_closure(db, month, contract_ids=[], uninstall_event_ids=[fee.id], charge_item_ids=[])
        assert result['uninstall_fees_generated'] == 0
    assert db.query(Billing).count() == 1


def test_retry_does_not_duplicate_audit_or_replace_reason(http, db, fee):
    assert exclude(http, fee).status_code == 200
    db.refresh(fee)
    first_notes = fee.notes
    again = exclude(http, fee, 'Motivo diferente em uma repetição')
    assert again.status_code == 200 and again.json()['already_excluded'] is True
    db.refresh(fee)
    assert fee.notes == first_notes
    assert db.query(AuditLog).filter_by(entity_type='uninstall_event').count() == 1


@pytest.mark.parametrize('billing_status', [BillingStatus.PENDING, BillingStatus.PAID, BillingStatus.CANCELED])
def test_already_billed_fee_is_protected(http, db, fee, cliente, billing_status):
    billing = Billing(client_id=cliente.id, amount=Decimal('160.00'), due_date=date(2026, 11, 10),
        status=billing_status, billing_type='taxa_desinstalacao')
    db.add(billing)
    db.flush()
    fee.status = 'processed'
    fee.billing_id = billing.id
    db.commit()
    response = exclude(http, fee)
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'uninstall_fee_already_billed'
    db.refresh(fee)
    assert fee.status == 'processed' and fee.billing_id == billing.id
    assert db.query(AuditLog).count() == 0


@pytest.mark.parametrize('state,billing_link', [('processed', False), ('pending', True), ('unknown', False)])
def test_inconsistent_or_unknown_fee_state_is_not_excluded(http, db, fee, cliente, state, billing_link):
    if billing_link:
        billing = Billing(client_id=cliente.id, amount=160, due_date=date(2026, 11, 1), status=BillingStatus.PENDING)
        db.add(billing)
        db.flush()
        fee.billing_id = billing.id
    fee.status = state
    db.commit()
    assert exclude(http, fee).status_code == 409
    db.refresh(fee)
    assert fee.status == state


@pytest.mark.parametrize('reason', ['', '  ', 'ab', 'x' * 501, None])
def test_reason_is_required(http, db, fee, reason):
    assert exclude(http, fee, reason).status_code == 422
    db.refresh(fee)
    assert fee.status == 'pending'


@pytest.mark.parametrize('fixture,status', [('http_fin', 200), ('http_op', 403), ('http_cliente', 403), ('http_unauth', 401)])
def test_permissions(request, fixture, status, fee):
    assert exclude(request.getfixturevalue(fixture), fee).status_code == status


def test_missing_fee_returns_404(http):
    assert http.post('/api/v1/billing-closure/uninstall-events/999999/exclude', json={'reason': 'Taxa já paga'}).status_code == 404


def test_commit_failure_preserves_pending_fee(http, db, fee, monkeypatch):
    def fail_commit():
        raise RuntimeError('simulated commit failure')
    monkeypatch.setattr(db, 'commit', fail_commit)
    assert exclude(http, fee).status_code == 500
    db.refresh(fee)
    assert fee.status == 'pending' and fee.notes == 'Retirada registrada'
    assert db.query(AuditLog).count() == 0


@pytest.mark.parametrize('new_state', ['canceled', 'processed'])
def test_generation_revalidates_fee_after_simulation_before_any_billing(db, fee, monkeypatch, new_state):
    import app.services.billing_closure as closure
    real_simulate = closure.simulate_closure
    def simulate_then_change(*args, **kwargs):
        preview = real_simulate(*args, **kwargs)
        db.execute(update(UninstallEvent).where(UninstallEvent.id == fee.id)
            .values(status=new_state).execution_options(synchronize_session=False))
        return preview
    monkeypatch.setattr(closure, 'simulate_closure', simulate_then_change)
    with pytest.raises(ValueError, match='Refaça a simulação'):
        execute_closure(db, date(2026, 10, 1), contract_ids=[], uninstall_event_ids=[fee.id], charge_item_ids=[])
    assert db.query(Billing).count() == 0


def test_excluding_large_fee_recalculates_small_fee_accumulation(http, db, fee):
    small = UninstallEvent(vehicle_id=fee.vehicle_id, client_id=fee.client_id,
        payer_client_id=fee.payer_client_id, uninstall_date=fee.uninstall_date,
        fee_amount=Decimal('3.00'), status='pending')
    db.add(small)
    db.commit()
    before = simulate_closure(db, date(2026, 10, 1))
    assert before['total_uninstall_fees'] == 163
    assert exclude(http, fee).status_code == 200
    after = simulate_closure(db, date(2026, 10, 1))
    assert after['total_uninstall_fees'] == 0
    assert [item['event_id'] for item in after['uninstall_events']] == [small.id]
    assert after['uninstall_events'][0]['deferred'] is True
