from sqlalchemy import ForeignKey, Text, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class BillingChangeLog(Base, TimestampMixin):
    __tablename__ = 'billing_change_logs'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    billing_id: Mapped[int] = mapped_column(ForeignKey('billings.id'), index=True)
    changed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    field_name: Mapped[str] = mapped_column(String(40), index=True)
    previous_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    justification: Mapped[str] = mapped_column(Text)
