"""Quota compartilhada entre os processos que enviam pela mesma conta SMTP."""
from datetime import datetime

from sqlalchemy import DateTime, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SmtpBudget(Base):
    __tablename__ = 'smtp_budgets'

    account: Mapped[str] = mapped_column(String(64), primary_key=True)
    next_allowed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
