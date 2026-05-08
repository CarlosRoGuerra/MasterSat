from sqlalchemy import Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class TrackerHistory(Base, TimestampMixin):
    __tablename__ = 'tracker_histories'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    tracker_id: Mapped[int] = mapped_column(ForeignKey('trackers.id'), index=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    previous_vehicle_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_vehicle_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_client_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_client_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    event_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
