from sqlalchemy import Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class AuditLog(Base, TimestampMixin):
    __tablename__ = 'audit_logs'
    __table_args__ = (Index(
        'uq_audit_logs_event_id', 'event_id', unique=True,
        postgresql_where=text('event_id IS NOT NULL'),
        sqlite_where=text('event_id IS NOT NULL'),
    ),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    user_role: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    method: Mapped[str] = mapped_column(String(10), index=True)
    path: Mapped[str] = mapped_column(String(255), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(60), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
