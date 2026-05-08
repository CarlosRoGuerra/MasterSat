from datetime import date
from sqlalchemy import Date, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import SoftDeleteMixin, TimestampMixin
from app.models.enums import BillingStatus
from app.db.session import Base


class Billing(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = 'billings'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey('contracts.id'), nullable=True, index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id'), index=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey('client_charge_items.id'), nullable=True, index=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey('vehicles.id'), nullable=True, index=True)
    tracker_id: Mapped[int | None] = mapped_column(ForeignKey('trackers.id'), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    billing_type: Mapped[str] = mapped_column(String(30), default='recorrente', index=True)
    installment_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    installment_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    due_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[BillingStatus] = mapped_column(Enum(BillingStatus), default=BillingStatus.PENDING, index=True)
    payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    paid_amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    receipt_number: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    period_label: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
