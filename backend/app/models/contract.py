from datetime import date
from decimal import Decimal
from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import SoftDeleteMixin, TimestampMixin
from app.db.session import Base


class Contract(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = 'contracts'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id'), index=True)
    # Interveniente financeiro: outro cliente da base que responde pela cobrança
    # deste contrato (evita cadastro duplicado). None = o próprio cliente.
    interveniente_client_id: Mapped[int | None] = mapped_column(ForeignKey('clients.id'), nullable=True, index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey('plans.id'), index=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey('vehicles.id'), nullable=True, index=True)
    tracker_id: Mapped[int | None] = mapped_column(ForeignKey('trackers.id'), nullable=True, index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='ativo')
    billing_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    billing_modality: Mapped[str] = mapped_column(String(20), default='boleto')
    # Banco emissor da cobrança deste contrato (hoje a emissão integrada é Ailos)
    bank: Mapped[str | None] = mapped_column(String(40), nullable=True, default='ailos')
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Taxas do TERMO DE ADESÃO (por veículo)
    installation_fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    uninstall_fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Assinatura do contrato físico (reunião de 07/08/2026): a empresa continua
    # colhendo assinatura em papel, então o sistema registra se já voltou
    # assinado. O PDF digitalizado vai nos documentos do cliente, categoria
    # "contrato".
    signed: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    signed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
