from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class SicrediApiLog(Base, TimestampMixin):
    """Log de cada chamada à API de Cobrança Sicredi (payloads mascarados)."""

    __tablename__ = 'sicredi_api_logs'

    id: Mapped[int] = mapped_column(primary_key=True)
    ambiente: Mapped[str] = mapped_column(String(10))
    endpoint: Mapped[str] = mapped_column(String(255))
    method: Mapped[str] = mapped_column(String(10))
    request_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    billing_id: Mapped[int | None] = mapped_column(ForeignKey('billings.id'), nullable=True)
