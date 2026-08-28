from sqlalchemy import Date, Enum, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import SoftDeleteMixin, TimestampMixin
from app.models.enums import TrackerStatus


class Tracker(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = 'trackers'
    __table_args__ = (
        Index(
            'uq_trackers_imei_active',
            'imei',
            unique=True,
            postgresql_where=text('is_deleted = false'),
            sqlite_where=text('is_deleted = 0'),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    imei: Mapped[str] = mapped_column(String(50))
    serial_number: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    brand: Mapped[str | None] = mapped_column(String(60), nullable=True)
    model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[TrackerStatus] = mapped_column(Enum(TrackerStatus), default=TrackerStatus.STOCK)

    sim_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sim_iccid: Mapped[str | None] = mapped_column(String(40), nullable=True)
    carrier: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sim_status: Mapped[str | None] = mapped_column(String(30), nullable=True)

    firmware: Mapped[str | None] = mapped_column(String(60), nullable=True)
    external_manufacturer_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    external_manufacturer_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(60), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    install_location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    chip_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    equipment_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    communication_type: Mapped[int | None] = mapped_column(Integer, nullable=True)

    service_plan_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    installation_fee: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)

    acquisition_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    install_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    uninstall_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    warranty_until: Mapped[Date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    integration_status: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    integration_last_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    integration_last_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    integration_last_transaction_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    client_id: Mapped[int | None] = mapped_column(ForeignKey('clients.id'), nullable=True, index=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey('vehicles.id'), nullable=True, index=True)
