from datetime import date
from sqlalchemy import Date, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import SoftDeleteMixin, TimestampMixin
from app.models.enums import BillingStatus
from app.db.session import Base
class Billing(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__='billings'
    id: Mapped[int]=mapped_column(primary_key=True, index=True)
    contract_id: Mapped[int|None]=mapped_column(ForeignKey('contracts.id'), nullable=True)
    client_id: Mapped[int]=mapped_column(ForeignKey('clients.id'))
    amount: Mapped[float]=mapped_column(Numeric(10,2))
    due_date: Mapped[date]=mapped_column(Date)
    status: Mapped[BillingStatus]=mapped_column(Enum(BillingStatus), default=BillingStatus.PENDING)
    payment_date: Mapped[date|None]=mapped_column(Date, nullable=True)
    payment_method: Mapped[str|None]=mapped_column(String(40), nullable=True)
    notes: Mapped[str|None]=mapped_column(Text, nullable=True)
