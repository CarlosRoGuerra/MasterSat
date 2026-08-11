from decimal import Decimal

from sqlalchemy import Boolean, Integer, Numeric, String, Text
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
    billing_interval_months: Mapped[int]=mapped_column(Integer, default=1)
    # Padrões do plano usados para pré-preencher o contrato (taxas por veículo,
    # dia de vencimento e vigência em meses). Todos opcionais — o contrato pode
    # sobrescrever caso a negociação fuja do padrão.
    default_installation_fee: Mapped[Decimal|None]=mapped_column(Numeric(10,2), nullable=True)
    default_uninstall_fee: Mapped[Decimal|None]=mapped_column(Numeric(10,2), nullable=True)
    default_billing_day: Mapped[int|None]=mapped_column(Integer, nullable=True)
    default_duration_months: Mapped[int|None]=mapped_column(Integer, nullable=True)
