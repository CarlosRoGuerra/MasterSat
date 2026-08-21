"""Data/hora no fuso de Brasília — a fonte de verdade do negócio.

O container roda em UTC por padrão; usar ``date.today()`` ingênuo faz um boleto
que vence "hoje" virar VENCIDA um dia antes perto da meia-noite (UTC está 3h à
frente). Vencimento, inadimplência e juros DEVEM decidir pela data de Brasília.

Offset fixo -03:00: o Brasil não tem horário de verão desde 2019 (Decreto
9.772/2019), então America/Sao_Paulo é constante hoje. Fixo evita depender de
tzdata na imagem slim. Se o horário de verão voltar, trocar por ZoneInfo.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

BRASILIA_TZ = timezone(timedelta(hours=-3))


def agora() -> datetime:
    """Datetime timezone-aware no fuso de Brasília."""
    return datetime.now(BRASILIA_TZ)


def hoje() -> date:
    """Data corrente em Brasília (para vencimento/inadimplência)."""
    return agora().date()
