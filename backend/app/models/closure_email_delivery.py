from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class ClosureEmailDelivery(Base, TimestampMixin):
    """Resultado durável do envio de um título de um lote de fechamento."""

    __tablename__ = 'closure_email_deliveries'
    __table_args__ = (
        UniqueConstraint('closure_job_id', 'billing_id', name='uq_closure_email_delivery_title'),
        Index('ix_closure_delivery_queue', 'status', 'id'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    closure_job_id: Mapped[int] = mapped_column(ForeignKey('closure_jobs.id'), index=True)
    billing_id: Mapped[int] = mapped_column(ForeignKey('billings.id'), index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
