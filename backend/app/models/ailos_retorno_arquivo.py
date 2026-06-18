from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class AilosRetornoArquivo(Base, TimestampMixin):
    """Solicitação/download de arquivo de retorno (ZIP) da API Ailos."""

    __tablename__ = 'ailos_retorno_arquivos'

    id: Mapped[int] = mapped_column(primary_key=True)
    numero_convenio: Mapped[str] = mapped_column(String(20))
    data_movimento: Mapped[date] = mapped_column(Date)
    ticket: Mapped[str | None] = mapped_column(String(60), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='requested')
    storage_object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
