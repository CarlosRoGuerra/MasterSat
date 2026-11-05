"""Adesões, agendamentos e recebimentos da API Pix (separados dos boletos)."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class SicrediPixRecorrencia(Base, TimestampMixin):
    __tablename__ = 'sicredi_pix_recorrencias'
    __table_args__ = (
        Index('uq_sicredi_pix_recorrencia_ativa', 'client_id', 'ambiente', unique=True,
              postgresql_where=text('ativa = true'), sqlite_where=text('ativa = 1')),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id'), index=True)
    ambiente: Mapped[str] = mapped_column(String(20))
    referencia: Mapped[str] = mapped_column(String(35), unique=True)
    id_rec: Mapped[str | None] = mapped_column(String(29), unique=True)
    location_id: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default='PREPARANDO')
    ativa: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    data_inicial: Mapped[date] = mapped_column(Date)
    objeto: Mapped[str] = mapped_column(String(35))
    valor_minimo: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    politica_retentativa: Mapped[str] = mapped_column(String(20), default='NAO_PERMITE')
    pix_copia_cola: Mapped[str | None] = mapped_column(Text)
    erro: Mapped[str | None] = mapped_column(Text)
    consultada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processar_evento: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SicrediPixCobranca(Base, TimestampMixin):
    __tablename__ = 'sicredi_pix_cobrancas'
    __table_args__ = (
        Index('uq_sicredi_pix_cobranca_ciclo', 'recorrencia_id', 'ciclo', unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    billing_id: Mapped[int] = mapped_column(ForeignKey('billings.id'), unique=True)
    recorrencia_id: Mapped[int] = mapped_column(ForeignKey('sicredi_pix_recorrencias.id'), index=True)
    txid: Mapped[str] = mapped_column(String(35), unique=True)
    ciclo: Mapped[date] = mapped_column(Date)
    vencimento: Mapped[date] = mapped_column(Date)
    valor: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(30), default='NA_FILA', index=True)
    enviada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consultada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    proxima_tentativa: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    erro: Mapped[str | None] = mapped_column(Text)
    pendencia: Mapped[str | None] = mapped_column(Text)
    processar_evento: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SicrediPixRecebimento(Base, TimestampMixin):
    __tablename__ = 'sicredi_pix_recebimentos'

    id: Mapped[int] = mapped_column(primary_key=True)
    cobranca_id: Mapped[int] = mapped_column(ForeignKey('sicredi_pix_cobrancas.id'), index=True)
    end_to_end_id: Mapped[str] = mapped_column(String(32), unique=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    horario: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    aplicado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pendencia: Mapped[str | None] = mapped_column(Text)
