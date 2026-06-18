from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class AilosClientToken(Base, TimestampMixin):
    """Token de aplicação (client_credentials, APIM/WSO2) por ambiente."""

    __tablename__ = 'ailos_client_tokens'

    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    token_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
