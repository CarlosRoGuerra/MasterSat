from __future__ import annotations

from datetime import date

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.client import Client


def _lock_competencia(db: Session, reference_month: date) -> None:
    """Serializa fechamentos concorrentes da MESMA competência.

    Sem isto, dois fechamentos simultâneos do mesmo mês veem ``already_generated``
    False ao mesmo tempo e ambos inserem — duplicando as cobranças. O lock de
    transação (Postgres) segura o segundo até o primeiro comitar; em SQLite
    (testes, serial) é no-op.
    """
    if db.bind is None or db.bind.dialect.name != 'postgresql':
        return
    # Chave estável por competência (AAAAMM) — meses diferentes não se bloqueiam.
    chave = reference_month.year * 100 + reference_month.month
    db.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': chave})


def _apply_client_scope(query, client_column, filter_type: str, client_id: int | None):
    """Aplica o mesmo recorte de clientes a qualquer categoria do fechamento."""
    eligible_clients = select(Client.id).where(Client.is_deleted.is_(False))
    if filter_type in ('pf', 'pj'):
        eligible_clients = eligible_clients.where(Client.type == filter_type)
    elif filter_type == 'client' and client_id is not None:
        eligible_clients = eligible_clients.where(Client.id == client_id)
    return query.filter(client_column.in_(eligible_clients))
