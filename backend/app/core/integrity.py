from __future__ import annotations

from typing import Mapping, NoReturn

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def integrity_conflict_detail(
    exc: IntegrityError,
    messages: Mapping[str, str],
    *,
    sqlite_columns: Mapping[str, str] | None = None,
) -> str | None:
    """Return the domain detail only for an explicitly known constraint."""

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
    """

    db.rollback()

    detail = integrity_conflict_detail(
        exc,
        messages,
        sqlite_columns=sqlite_columns,
    )
    if detail is not None:
        raise HTTPException(status_code=409, detail=detail) from exc

    raise exc
