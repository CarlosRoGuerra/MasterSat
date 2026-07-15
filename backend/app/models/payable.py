from datetime import date

from sqlalchemy import Date, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import SoftDeleteMixin, TimestampMixin


class Payable(Base, TimestampMixin, SoftDeleteMixin):
    """Conta a PAGAR da empresa (fornecedores, aluguel, chips, impostos etc.).

    Contraparte do contas a receber (billings). Fluxo simples:
    pendente → paga (com data/forma) ou cancelada.
    """

    __tablename__ = 'payables'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    description: Mapped[str] = mapped_column(String(200), index=True)
    supplier: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    category: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    due_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), default='pendente', index=True)  # pendente | paga | cancelada
    payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
