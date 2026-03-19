from datetime import datetime
from sqlalchemy import DateTime, Enum, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import SoftDeleteMixin, TimestampMixin
from app.models.enums import OrderStatus, OrderType
from app.db.session import Base
class ServiceOrder(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__='service_orders'
    id: Mapped[int]=mapped_column(primary_key=True, index=True)
    number: Mapped[str]=mapped_column(String(30), unique=True, index=True)
    type: Mapped[OrderType]=mapped_column(Enum(OrderType))
    status: Mapped[OrderStatus]=mapped_column(Enum(OrderStatus), default=OrderStatus.OPEN)
    client_id: Mapped[int]=mapped_column(ForeignKey('clients.id'))
    vehicle_id: Mapped[int|None]=mapped_column(ForeignKey('vehicles.id'), nullable=True)
    tracker_id: Mapped[int|None]=mapped_column(ForeignKey('trackers.id'), nullable=True)
    technician_id: Mapped[int|None]=mapped_column(ForeignKey('users.id'), nullable=True)
    scheduled_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True), nullable=True)
    checklist: Mapped[dict|None]=mapped_column(JSON, nullable=True)
    observations: Mapped[str|None]=mapped_column(Text, nullable=True)
