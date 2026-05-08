from sqlalchemy import Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin
from app.models.enums import OrderStatus


class ServiceOrderStatusLog(Base, TimestampMixin):
    __tablename__ = 'service_order_status_logs'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    service_order_id: Mapped[int] = mapped_column(ForeignKey('service_orders.id'), index=True)
    previous_status: Mapped[OrderStatus | None] = mapped_column(Enum(OrderStatus), nullable=True)
    new_status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
