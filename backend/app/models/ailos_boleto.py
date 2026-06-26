from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class AilosBoleto(Base, TimestampMixin):
    """Boleto gerado via API de Cobrança Ailos, vinculado 1:1 a um Billing."""

    __tablename__ = 'ailos_boletos'

    id: Mapped[int] = mapped_column(primary_key=True)
    billing_id: Mapped[int] = mapped_column(ForeignKey('billings.id'), unique=True, index=True)
    lote_id: Mapped[int | None] = mapped_column(ForeignKey('ailos_lotes.id'), nullable=True)
    numero_convenio: Mapped[str] = mapped_column(String(20))
    numero_documento: Mapped[str | None] = mapped_column(String(40), nullable=True)
    nosso_numero: Mapped[str | None] = mapped_column(String(40), nullable=True)
    identificador_unico_titulo: Mapped[str | None] = mapped_column(String(60), nullable=True)
    linha_digitavel: Mapped[str | None] = mapped_column(String(60), nullable=True)
    codigo_barras: Mapped[str | None] = mapped_column(String(60), nullable=True)
    valor_nominal: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    data_vencimento: Mapped[date | None] = mapped_column(Date, nullable=True)
    status_ailos: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # "Copia e cola" (EMV) do Pix quando o boleto é BolePix e a conta tem a
    # chave Pix vinculada à funcionalidade na Ailos. Usado p/ gerar o QR.
    pix_emv: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_request: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    payload_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
