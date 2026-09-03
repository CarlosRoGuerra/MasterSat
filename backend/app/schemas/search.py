"""Schemas da Busca Global (Command Palette) — GET /api/v1/search.

Um item por resultado, agrupado por categoria em GlobalSearchOut. Os campos
ficam deliberadamente enxutos (só o necessário pra exibir e navegar) — não é
o mesmo *Out de cada listagem, que carrega o registro inteiro.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

SearchEntity = Literal['client', 'vehicle', 'tracker', 'service_order', 'contract', 'document']


class SearchResultItem(BaseModel):
    id: int
    entity: SearchEntity
    title: str
    subtitle: str | None = None
    status: str | None = None
    # IDs auxiliares só usados pelo frontend pra montar a querystring de
    # navegação (ex.: documento aponta pro cliente/veículo/OS dono dele).
    client_id: int | None = None
    vehicle_id: int | None = None
    service_order_id: int | None = None


class GlobalSearchOut(BaseModel):
    clients: list[SearchResultItem] = []
    vehicles: list[SearchResultItem] = []
    trackers: list[SearchResultItem] = []
    service_orders: list[SearchResultItem] = []
    contracts: list[SearchResultItem] = []
    documents: list[SearchResultItem] = []
