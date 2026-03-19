from datetime import date
from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import SoftDeleteMixin, TimestampMixin
from app.db.session import Base
class Contract(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__='contracts'
    id: Mapped[int]=mapped_column(primary_key=True, index=True)
    client_id: Mapped[int]=mapped_column(ForeignKey('clients.id'))
    plan_id: Mapped[int]=mapped_column(ForeignKey('plans.id'))
    start_date: Mapped[date]=mapped_column(Date)
    end_date: Mapped[date|None]=mapped_column(Date, nullable=True)
    status: Mapped[str]=mapped_column(String(30), default='ativo')
