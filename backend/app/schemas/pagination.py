"""Envelope genérico para listagens paginadas por offset (skip/limit) — ver BE-02.

Usado só onde skip/limit é paginação de verdade (clients, trackers, vehicles).
Endpoints que só limitam um teto (ex.: billings, contracts) continuam
devolvendo list[X] puro — não é paginação, é um limite de segurança.
"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar('T')


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
