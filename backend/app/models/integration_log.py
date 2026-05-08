from sqlalchemy import Boolean, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class IntegrationLog(Base, TimestampMixin):
    __tablename__ = 'integration_logs'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    provider: Mapped[str] = mapped_column(String(40), default='multiportal', index=True)
    batch_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(30), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    operation: Mapped[str] = mapped_column(String(80), index=True)
    transaction_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    response_code: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    response_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_payload: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    response_payload: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
