from __future__ import annotations

from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class SicrediLote(Base, TimestampMixin):
    """Fila persistente: cada parcela usa o registro individual idempotente."""

    __tablename__ = 'sicredi_lotes'

    id: Mapped[int] = mapped_column(primary_key=True)
    chave: Mapped[str] = mapped_column(String(64), unique=True)
    ticket: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    tipo: Mapped[str] = mapped_column(String(10))
    ambiente: Mapped[str] = mapped_column(String(10))
    cooperativa: Mapped[str] = mapped_column(String(4))
    posto: Mapped[str] = mapped_column(String(2))
    codigo_beneficiario: Mapped[str] = mapped_column(String(5))
    payer_client_id: Mapped[int | None] = mapped_column(ForeignKey('clients.id'), nullable=True, index=True)
    billing_ids: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default='processing', index=True)
    erros: Mapped[dict | None] = mapped_column(JSON, nullable=True)
