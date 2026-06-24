from datetime import date
from decimal import Decimal
from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import SoftDeleteMixin, TimestampMixin
from app.db.session import Base


class Contract(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = 'contracts'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id'), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey('plans.id'), index=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey('vehicles.id'), nullable=True, index=True)
    tracker_id: Mapped[int | None] = mapped_column(ForeignKey('trackers.id'), nullable=True, index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='ativo')
    billing_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    billing_modality: Mapped[str] = mapped_column(String(20), default='boleto')
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Taxas do TERMO DE ADESÃO (por veículo)
    installation_fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    uninstall_fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
