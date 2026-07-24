from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class NfseLote(Base, TimestampMixin):
    """
    Lote de emissão de NFS-e em massa, a partir de um fechamento financeiro.

    Agrupa vários ``NfseNota`` (1 por cobrança) emitidos numa mesma sessão, com
    os parâmetros fiscais comuns aplicados a todas. O processamento é
    assíncrono: o lote nasce ``processando`` e uma thread emite cada nota,
    atualizando os contadores. A situação geral é derivada dos contadores.

    status:
      processando — thread ainda emitindo as notas do lote
      concluido   — todas as notas do lote foram autorizadas
      com_erro    — ao menos uma nota falhou (ver cada NfseNota)
    """

    __tablename__ = 'nfse_lotes'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Lote de fechamento de origem (period_label do faturamento, ex.: '07/2026')
    period_label: Mapped[str] = mapped_column(String(20), index=True)

    # Parâmetros fiscais comuns aplicados a todas as notas da sessão
    competencia: Mapped[date | None] = mapped_column(Date, nullable=True)
    codigo_servico: Mapped[str | None] = mapped_column(String(10), nullable=True)
    discriminacao: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default='processando', index=True)

    total_notas: Mapped[int] = mapped_column(Integer, default=0)
    total_autorizadas: Mapped[int] = mapped_column(Integer, default=0)
    total_erro: Mapped[int] = mapped_column(Integer, default=0)

    criado_por: Mapped[int | None] = mapped_column(Integer, nullable=True)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
