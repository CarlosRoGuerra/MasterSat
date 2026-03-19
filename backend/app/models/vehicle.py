from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import SoftDeleteMixin, TimestampMixin
from app.models.enums import VehicleStatus
from app.db.session import Base
class Vehicle(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__='vehicles'
    id: Mapped[int]=mapped_column(primary_key=True, index=True)
    plate: Mapped[str]=mapped_column(String(10), unique=True, index=True)
    chassis: Mapped[str|None]=mapped_column(String(40), nullable=True)
    renavam: Mapped[str|None]=mapped_column(String(20), nullable=True)
    brand: Mapped[str|None]=mapped_column(String(60), nullable=True)
    model: Mapped[str|None]=mapped_column(String(60), nullable=True)
    year: Mapped[int|None]=mapped_column(Integer, nullable=True)
    color: Mapped[str|None]=mapped_column(String(30), nullable=True)
    type: Mapped[str|None]=mapped_column(String(30), nullable=True)
    status: Mapped[VehicleStatus]=mapped_column(Enum(VehicleStatus), default=VehicleStatus.ACTIVE)
    client_id: Mapped[int]=mapped_column(ForeignKey('clients.id'))
