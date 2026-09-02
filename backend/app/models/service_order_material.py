from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import SoftDeleteMixin, TimestampMixin


class ServiceOrderMaterial(Base, TimestampMixin, SoftDeleteMixin):
    """Material/peça usado na execução de uma OS — registro operacional,
    não gera cobrança automática (decisão de escopo desta fase)."""

    __tablename__ = 'service_order_materials'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    service_order_id: Mapped[int] = mapped_column(ForeignKey('service_orders.id'), index=True)
    # Link opcional só de conveniência (autocomplete no frontend contra o
    # catálogo existente) — descrição/preço continuam livres mesmo sem ele.
    service_product_id: Mapped[int | None] = mapped_column(ForeignKey('service_products.id'), nullable=True)
    description: Mapped[str] = mapped_column(String(300))
    quantity: Mapped[float] = mapped_column(Numeric(10, 2), default=1)
    unit: Mapped[str | None] = mapped_column(String(10), nullable=True)
    unit_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
