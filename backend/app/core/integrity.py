from __future__ import annotations

import logging
from typing import Mapping, NoReturn

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _resolve_constraint_name(
    exc: IntegrityError,
    *,
    sqlite_columns: Mapping[str, str] | None = None,
) -> str | None:
    orig = exc.orig
    diag = getattr(orig, "diag", None)
    constraint_name = getattr(diag, "constraint_name", None)

    if constraint_name is None and sqlite_columns:
        database_message = str(orig)
        constraint_name = next(
            (
                mapped_name
                for signature, mapped_name in sqlite_columns.items()
                if signature in database_message
            ),
            None,
        )

    return constraint_name


def integrity_conflict_detail(
    exc: IntegrityError,
    messages: Mapping[str, str],
    *,
    sqlite_columns: Mapping[str, str] | None = None,
) -> str | None:
    """Return the domain detail only for an explicitly known constraint."""

    constraint_name = _resolve_constraint_name(exc, sqlite_columns=sqlite_columns)
    return messages.get(constraint_name) if constraint_name else None


def raise_integrity_conflict(
    db: Session,
    exc: IntegrityError,
    messages: Mapping[str, str],
    *,
    sqlite_columns: Mapping[str, str] | None = None,
) -> NoReturn:
    """Rollback and translate only explicitly known integrity violations.

    This is intentionally not a global exception handler. Each caller owns the
    constraint-to-domain-message mapping for its operation. SQLite does not
    expose constraint names, so tests may opt into narrowly scoped signatures.

    Always rolls back first — a session left with a failed statement is
    unusable for anything else until it does, in both Postgres (transaction
    aborted until ROLLBACK) and SQLAlchemy's own bookkeeping. The raw driver
    error is logged server-side either way (it may contain the offending
    value, e.g. a duplicated CPF/CNPJ) but never reaches the HTTP response —
    the client only ever sees the mapped domain message, or a generic 500
    for anything not explicitly mapped here.
    """

    db.rollback()

    constraint_name = _resolve_constraint_name(exc, sqlite_columns=sqlite_columns)
    detail = messages.get(constraint_name) if constraint_name else None

    if detail is not None:
        logger.warning(
            "Integrity conflict translated to 409: constraint=%s detail=%r db_error=%s",
            constraint_name, detail, exc.orig,
        )
        raise HTTPException(status_code=409, detail=detail) from exc

    logger.error(
        "Unmapped IntegrityError (constraint=%s): db_error=%s",
        constraint_name, exc.orig, exc_info=True,
    )
    raise exc
