from datetime import datetime, timedelta, timezone
import smtplib

import pytest
from sqlalchemy.orm import Session

from app.models.closure_email_delivery import ClosureEmailDelivery
from app.models.closure_job import ClosureJob
from app.models.enums import BillingStatus
from app.models.smtp_budget import SmtpBudget
from app.services import closure_delivery, email_smtp, smtp_rate_limit
from tests.test_closure_delivery import _billing, _client, PREFIX

CFG = {'host': 'smtp.test', 'username': 'envio@example.test',
       'from_email': 'envio@example.test', 'from_name': ''}


@pytest.fixture
def clock(monkeypatch):
    value = [datetime(2026, 10, 5, 12, tzinfo=timezone.utc)]
    monkeypatch.setattr(smtp_rate_limit, 'utcnow', lambda: value[0])
    return value


@pytest.fixture
def smtp(monkeypatch):
    messages = []

    class Server:
        def send_message(self, message):
            messages.append(message)
            return {}

        def quit(self):
            pass

    monkeypatch.setattr(email_smtp, '_abrir_conexao', lambda _: Server())
    monkeypatch.setattr(email_smtp, 'load_config', lambda _: CFG)
    from app.api.v1.endpoints import boletos
    monkeypatch.setattr(boletos, '_montar_pdf_boleto', lambda *args: (b'%PDF-test', 'boleto.pdf'))
    return messages


def batch(db, count=2):
    payer = _client(db, 'Cliente da fila', '55555555555', 'nao')
    payer.extra_emails = ['extra@example.test', payer.email.upper()]
    bills = [_billing(db, payer) for _ in range(count)]
    lote = ClosureJob(reference_month='2026-09', status='completed',
                      result={'payment_billing_ids': [b.id for b in bills]})
    db.add(lote)
    db.commit()
    return lote, bills


def test_spacing_counts_recipients_and_survives_new_sessions(db, clock, smtp):
    email_smtp.enviar_email(db, 'a@test.com, b@test.com, a@test.com', 'Teste', 'Corpo', config=CFG)
    with Session(db.get_bind()) as other:
        with pytest.raises(email_smtp.EmailRateLimitError) as error:
            email_smtp.enviar_email(other, 'c@test.com', 'Teste', 'Corpo', config=CFG)
        assert error.value.retry_at == clock[0] + timedelta(seconds=80)
    assert len(smtp) == 1
    clock[0] += timedelta(seconds=80)
    email_smtp.enviar_email(db, 'c@test.com', 'Teste', 'Corpo', config=CFG)
    assert len(smtp) == 2
    assert sum(a['count'] for a in db.query(SmtpBudget).one().attempts) == 3


def test_rolling_window_blocks_large_message_even_when_interval_elapsed(db, clock):
    for _ in range(89):
        smtp_rate_limit.check_budget(db, CFG, 1, reserve=True)
        clock[0] += timedelta(seconds=40)
    # Espaçamento venceu, mas há 89 destinatários na última hora.
    with pytest.raises(smtp_rate_limit.RateLimited) as error:
        smtp_rate_limit.check_budget(db, CFG, 3, reserve=True)
    assert error.value.retry_at == datetime(2026, 10, 5, 13, 0, 41, tzinfo=timezone.utc)
    clock[0] = error.value.retry_at
    smtp_rate_limit.check_budget(db, CFG, 3, reserve=True)
    with pytest.raises(ValueError):
        smtp_rate_limit.check_budget(db, CFG, 91, reserve=True)


def test_batch_is_durable_idempotent_and_waits_before_second_client(http, db, clock, smtp):
    lote, bills = batch(db, 31)
    payload = {'billing_ids': [b.id for b in bills]}
    url = f'{PREFIX}/{lote.id}/enviar'
    response = http.post(url, json=payload)
    assert response.status_code == 202, response.text
    assert len(response.json()['enfileirados']) == 31
    assert smtp == []
    assert http.get(f'{PREFIX}/{lote.id}').json()['em_fila'] == 31
    assert http.post(url, json=payload).json()['enfileirados'] == []
    assert db.query(ClosureEmailDelivery).count() == 31
    # Sessão do worker independente do request/navegador.
    with Session(db.get_bind()) as worker:
        closure_delivery.processar_fila(worker)
        closure_delivery.processar_fila(worker)
    assert len(smtp) == 1
    db.expire_all()
    waiting = db.query(ClosureEmailDelivery).filter_by(billing_id=bills[1].id).one()
    assert waiting.status == 'aguardando'
    assert smtp_rate_limit.utc(waiting.next_attempt_at) == clock[0] + timedelta(seconds=80)
    clock[0] += timedelta(seconds=79)
    closure_delivery.processar_fila(db)
    assert len(smtp) == 1
    clock[0] += timedelta(seconds=1)
    closure_delivery.processar_fila(db)
    assert len(smtp) == 2
    assert http.get(f'{PREFIX}/{lote.id}').json()['enviados'] == 2


def test_quota_rejection_pauses_account_for_61_minutes_then_resumes(http, db, clock, smtp, monkeypatch):
    lote, bills = batch(db)
    http.post(f'{PREFIX}/{lote.id}/enviar', json={'billing_ids': [b.id for b in bills]})
    attempts = []

    class Blocked:
        def send_message(self, msg):
            attempts.append(msg)
            raise smtplib.SMTPRecipientsRefused({'a@test.com': (450, b'Policy Rejection- Quota exceeded')})

        def quit(self):
            pass

    connect = email_smtp._abrir_conexao
    monkeypatch.setattr(email_smtp, '_abrir_conexao', lambda _: Blocked())
    closure_delivery.processar_fila(db)
    first = db.query(ClosureEmailDelivery).order_by(ClosureEmailDelivery.id).first()
    assert first.status == 'aguardando'
    assert smtp_rate_limit.utc(first.next_attempt_at) == clock[0] + timedelta(minutes=61)
    clock[0] += timedelta(minutes=60)
    closure_delivery.processar_fila(db)
    # Até o envio avulso da mesma conta respeita a pausa, sem conectar ao SMTP.
    with pytest.raises(email_smtp.EmailRateLimitError):
        email_smtp.enviar_email(db, 'avulso@example.test', 'Avulso', 'Corpo', config=CFG)
    assert len(attempts) == 1
    clock[0] += timedelta(minutes=1)
    monkeypatch.setattr(email_smtp, '_abrir_conexao', connect)
    closure_delivery.processar_fila(db)
    assert len(smtp) == 1
    assert first.status == 'enviado'


def test_partial_quota_failure_never_resends_accepted_recipients(http, db, clock, smtp, monkeypatch):
    lote, bills = batch(db)
    http.post(f'{PREFIX}/{lote.id}/enviar', json={'billing_ids': [b.id for b in bills]})

    class Partial:
        def send_message(self, msg):
            smtp.append(msg)
            return {'extra@example.test': (450, b'Quota exceeded')}

        def quit(self):
            pass

    monkeypatch.setattr(email_smtp, '_abrir_conexao', lambda _: Partial())
    closure_delivery.processar_fila(db)
    closure_delivery.processar_fila(db)
    first, second = db.query(ClosureEmailDelivery).order_by(ClosureEmailDelivery.id).all()
    assert first.status == 'desconhecido'
    assert second.status == 'aguardando'
    assert len(smtp) == 1
    assert smtp_rate_limit.utc(second.next_attempt_at) == clock[0] + timedelta(minutes=61)
    assert http.post(f'{PREFIX}/{lote.id}/enviar/{bills[0].id}').status_code == 409


def test_revalidates_paid_charge_and_does_not_retry_orphan(http, db, clock, smtp):
    lote, bills = batch(db)
    http.post(f'{PREFIX}/{lote.id}/enviar', json={'billing_ids': [b.id for b in bills]})
    bills[0].status = BillingStatus.PAID
    second = db.query(ClosureEmailDelivery).filter_by(billing_id=bills[1].id).one()
    second.status = 'processando'
    second.started_at = clock[0] - timedelta(minutes=16)
    db.commit()
    closure_delivery.processar_fila(db)
    db.expire_all()
    assert smtp == []
    assert db.query(ClosureEmailDelivery).filter_by(billing_id=bills[0].id).one().status == 'erro'
    assert second.status == 'desconhecido'


def test_failed_pdf_can_be_corrected_and_queued_again(http, db, clock, smtp, monkeypatch):
    from app.api.v1.endpoints import boletos
    lote, bills = batch(db, 1)
    original = boletos._montar_pdf_boleto
    monkeypatch.setattr(boletos, '_montar_pdf_boleto', lambda *args: (_ for _ in ()).throw(ValueError('PDF indisponível')))
    url = f'{PREFIX}/{lote.id}/enviar/{bills[0].id}'
    http.post(url)
    closure_delivery.processar_fila(db)
    assert db.query(ClosureEmailDelivery).one().status == 'erro'
    assert smtp == []
    monkeypatch.setattr(boletos, '_montar_pdf_boleto', original)
    http.post(url)
    closure_delivery.processar_fila(db)
    assert db.query(ClosureEmailDelivery).one().status == 'enviado'


def test_queue_rejects_foreign_ids_and_is_restricted(http_op, db, smtp):
    lote, bills = batch(db)
    assert http_op.post(f'{PREFIX}/{lote.id}/enviar', json={'billing_ids': [bills[0].id]}).status_code == 403
    assert db.query(ClosureEmailDelivery).count() == 0


def test_queue_selection_validation_is_atomic(http, db, smtp):
    lote, bills = batch(db)
    response = http.post(f'{PREFIX}/{lote.id}/enviar', json={'billing_ids': [bills[0].id, 999999]})
    assert response.status_code == 404
    assert http.post(f'{PREFIX}/{lote.id}/enviar', json={'billing_ids': []}).status_code == 422
    assert db.query(ClosureEmailDelivery).count() == 0
