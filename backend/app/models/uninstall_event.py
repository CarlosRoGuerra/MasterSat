from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class UninstallEvent(Base, TimestampMixin):
    """
    Evento de desinstalação pendente.
    Criado quando um rastreador é desvinculado de um veículo.
    O motor de fechamento mensal varre esses eventos e injeta a taxa como billing.
    """

    __tablename__ = 'uninstall_events'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey('vehicles.id'), index=True)
    tracker_id: Mapped[int | None] = mapped_column(ForeignKey('trackers.id'), nullable=True, index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey('contracts.id'), nullable=True)
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id'), index=True)
    # Snapshot do responsável financeiro na retirada; o contrato pode ser
    # alterado/cancelado antes de o fechamento transformar o evento em cobrança.
    payer_client_id: Mapped[int | None] = mapped_column(
        ForeignKey('clients.id'), nullable=True, index=True,
    )
    uninstall_date: Mapped[date] = mapped_column(Date, index=True)
    fee_amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    service_product_id: Mapped[int | None] = mapped_column(ForeignKey('service_products.id'), nullable=True)
    # pending → processed (billing gerado). ``skipped`` é legado e é recuperado
    # pelo fechamento para voltar à acumulação; valores pequenos não são perdidos.
    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    billing_id: Mapped[int | None] = mapped_column(ForeignKey('billings.id'), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
