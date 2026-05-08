from sqlalchemy import Date, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import SoftDeleteMixin, TimestampMixin
from app.models.enums import VehicleStatus


class Vehicle(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = 'vehicles'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id'), index=True)

    sales_point: Mapped[str | None] = mapped_column(String(120), nullable=True)
    seller_consultant: Mapped[str | None] = mapped_column(String(120), nullable=True)
    vehicle_classification: Mapped[str | None] = mapped_column(String(80), nullable=True)
    user_alert: Mapped[str | None] = mapped_column(Text, nullable=True)

    contract_number: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    contract_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    contract_end_date: Mapped[Date | None] = mapped_column(Date, nullable=True)

    address_zip_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    address_line: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address_complement: Mapped[str | None] = mapped_column(String(120), nullable=True)
    neighborhood: Mapped[str | None] = mapped_column(String(120), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(2), nullable=True)

    plate: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    chassis: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True)
    renavam: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    brand: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manufacture_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    fuel_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    fipe_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    fipe_value: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)

    status: Mapped[VehicleStatus] = mapped_column(Enum(VehicleStatus), default=VehicleStatus.ACTIVE)
    uninstalled_at: Mapped[Date | None] = mapped_column(Date, nullable=True)
