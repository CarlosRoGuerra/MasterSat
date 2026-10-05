"""Espaçamento e janela móvel por destinatário, persistentes entre reinícios.

Reserva antes de abrir a conexão SMTP. Tentativas incertas também consomem
quota; jamais devolvemos posições que o provedor possa ter contabilizado.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import RLock

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.smtp_budget import SmtpBudget

_local_lock = RLock()  # SQLite em desenvolvimento; Postgres usa FOR UPDATE.


def utcnow():
    return datetime.now(timezone.utc)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def hourly_limit():
    return max(1, min(90, settings.smtp_recipients_per_hour))


def account_key(config):
    account = (config.get('username') or config['from_email']).strip().lower()
    return sha256(account.encode()).hexdigest()


class RateLimited(Exception):
    def __init__(self, retry_at):
        self.retry_at = retry_at
        super().__init__('Envio aguardando o intervalo de segurança da conta de e-mail.')


def _row(db, config):
    if db.get_bind().dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    key = account_key(config)
    db.execute(insert(SmtpBudget).values(account=key, attempts=[]).on_conflict_do_nothing())
    return db.query(SmtpBudget).filter_by(account=key).with_for_update().one()


def check_budget(db: Session, config: dict, recipients: int, *, reserve=False):
    if recipients < 1 or recipients > hourly_limit():
        raise ValueError(f'O envio deve ter entre 1 e {hourly_limit()} destinatários.')
    now = utcnow()
    # Sessão própria: não confirma nem desfaz alterações do chamador.
    with _local_lock, Session(bind=db.get_bind()) as quota:
        row = _row(quota, config)
        recent = [a for a in row.attempts if datetime.fromisoformat(a['at']) > now - timedelta(hours=1)]
        retry_at = max(now, utc(row.next_allowed_at)) if row.next_allowed_at else now
        used = sum(a['count'] for a in recent)
        for attempt in recent:
            if used + recipients <= hourly_limit():
                break
            retry_at = max(retry_at, datetime.fromisoformat(attempt['at']) + timedelta(hours=1, seconds=1))
            used -= attempt['count']
        if retry_at > now:
            quota.commit()
            raise RateLimited(retry_at)
        if reserve:
            row.attempts = [*recent, {'at': now.isoformat(), 'count': recipients}]
            row.next_allowed_at = now + timedelta(seconds=3600 * recipients / hourly_limit())
        quota.commit()


def pause_after_quota(db: Session, config: dict):
    retry_at = utcnow() + timedelta(minutes=61)
    with _local_lock, Session(bind=db.get_bind()) as quota:
        row = _row(quota, config)
        row.next_allowed_at = max(utc(row.next_allowed_at), retry_at) if row.next_allowed_at else retry_at
        retry_at = row.next_allowed_at
        quota.commit()
    return retry_at
