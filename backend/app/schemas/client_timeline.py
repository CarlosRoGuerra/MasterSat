"""Schemas da Linha do Tempo do Cliente — GET /api/v1/clients/{id}/timeline.

Item enxuto (só o necessário pra exibir/agrupar/navegar), no mesmo espírito
de SearchResultItem em app/schemas/search.py — não é o *Out de cada listagem.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

TimelineCategory = Literal[
    'cliente', 'veiculo', 'rastreador', 'contrato', 'documento', 'financeiro', 'os', 'auditoria',
]

TimelineSeverity = Literal['info', 'success', 'warning', 'danger']

# Mesmo discriminador de entidade usado por SearchResultItem — permite que o
# frontend reaproveite buildSearchResultHref (lib/search-nav.ts) sem mapear
# rota nova nenhuma.
TimelineLinkEntity = Literal['client', 'vehicle', 'tracker', 'service_order', 'contract', 'document']


class TimelineLinkOut(BaseModel):
    entity: TimelineLinkEntity
    id: int
    client_id: int | None = None
    vehicle_id: int | None = None
    service_order_id: int | None = None


class TimelineEventOut(BaseModel):
    id: str
    category: TimelineCategory
    type: str
    occurred_at: datetime
    title: str
    description: str | None = None
    severity: TimelineSeverity = 'info'
    actor_name: str | None = None
    link: TimelineLinkOut | None = None
    metadata: dict[str, str] | None = None
