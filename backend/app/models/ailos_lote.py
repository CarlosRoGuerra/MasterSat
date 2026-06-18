from __future__ import annotations

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class AilosLote(Base, TimestampMixin):
    """Lote/carnê assíncrono enviado à API Ailos (rastreado por ticket)."""

    __tablename__ = 'ailos_lotes'

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(10))  # 'boleto' | 'carne'
    ticket: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    numero_convenio: Mapped[str] = mapped_column(String(20))
    billing_ids: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default='processing')
    payload_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
