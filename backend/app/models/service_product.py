from sqlalchemy import Boolean, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import SoftDeleteMixin, TimestampMixin


class ServiceProduct(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "service_products"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(40), default='servico', index=True)
    default_price: Mapped[float] = mapped_column(Numeric(10, 2))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_installments: Mapped[bool] = mapped_column(Boolean, default=True)
    remove_after_payment: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_add_on_uninstall: Mapped[bool] = mapped_column(Boolean, default=False)
