from sqlalchemy import Date, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import SoftDeleteMixin, TimestampMixin
from app.models.enums import TrackerStatus
from app.db.session import Base
class Tracker(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__='trackers'
    id: Mapped[int]=mapped_column(primary_key=True, index=True)
    imei: Mapped[str]=mapped_column(String(50), unique=True, index=True)
    brand: Mapped[str|None]=mapped_column(String(60), nullable=True)
    model: Mapped[str|None]=mapped_column(String(60), nullable=True)
    status: Mapped[TrackerStatus]=mapped_column(Enum(TrackerStatus), default=TrackerStatus.STOCK)
    sim_number: Mapped[str|None]=mapped_column(String(30), nullable=True)
    carrier: Mapped[str|None]=mapped_column(String(30), nullable=True)
    warranty_until: Mapped[Date|None]=mapped_column(Date, nullable=True)
    client_id: Mapped[int|None]=mapped_column(ForeignKey('clients.id'), nullable=True)
    vehicle_id: Mapped[int|None]=mapped_column(ForeignKey('vehicles.id'), nullable=True)
