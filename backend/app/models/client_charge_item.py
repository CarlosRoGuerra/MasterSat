from datetime import date
from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import SoftDeleteMixin, TimestampMixin


class ClientChargeItem(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "client_charge_items"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id'), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey('contracts.id'), nullable=True, index=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey('vehicles.id'), nullable=True, index=True)
    tracker_id: Mapped[int | None] = mapped_column(ForeignKey('trackers.id'), nullable=True, index=True)
    service_product_id: Mapped[int | None] = mapped_column(ForeignKey('service_products.id'), nullable=True, index=True)

    title: Mapped[str] = mapped_column(String(140), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2))
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2))
    installment_count: Mapped[int] = mapped_column(Integer, default=1)
    start_date: Mapped[date] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    remove_after_payment: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='ativo', index=True)
