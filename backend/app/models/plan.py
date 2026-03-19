from sqlalchemy import Boolean, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import SoftDeleteMixin, TimestampMixin
from app.db.session import Base
class Plan(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__='plans'
    id: Mapped[int]=mapped_column(primary_key=True, index=True)
    name: Mapped[str]=mapped_column(String(120), unique=True)
    price: Mapped[float]=mapped_column(Numeric(10,2))
    description: Mapped[str|None]=mapped_column(Text, nullable=True)
    active: Mapped[bool]=mapped_column(Boolean, default=True)
