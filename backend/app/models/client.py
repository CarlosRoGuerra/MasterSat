from sqlalchemy import Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import SoftDeleteMixin, TimestampMixin
from app.models.enums import ClientStatus


class Client(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = 'clients'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(180), index=True)
    cpf_cnpj: Mapped[str] = mapped_column(String(18), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(20), default='pf')
    status: Mapped[ClientStatus] = mapped_column(Enum(ClientStatus), default=ClientStatus.ACTIVE)
    email: Mapped[str | None] = mapped_column(String(180), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    address_line: Mapped[str | None] = mapped_column(String(180), nullable=True)
    address_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address_complement: Mapped[str | None] = mapped_column(String(120), nullable=True)
    neighborhood: Mapped[str | None] = mapped_column(String(120), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
