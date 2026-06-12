from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class ClosureJob(Base, TimestampMixin):
    """
    Rastreia jobs assíncronos de fechamento de faturamento.
    POST /billing-closure/generate retorna o job_id imediatamente;
    GET /billing-closure/jobs/{job_id} retorna o status e resultado.
    """

    __tablename__ = 'closure_jobs'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    reference_month: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    filter_type: Mapped[str] = mapped_column(String(20), default='all')
    client_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # queued → running → completed | failed
    status: Mapped[str] = mapped_column(String(20), default='queued', index=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
