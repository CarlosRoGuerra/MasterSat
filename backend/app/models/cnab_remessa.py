from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text,
    UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class CnabRemessa(Base, TimestampMixin):
    """Arquivo de remessa CNAB gerado (FIN-07).

    A remessa é um registro, não só um download: sequência por layout (o
    banco recusa sequência repetida), hash do arquivo e o próprio conteúdo —
    baixar de novo devolve os mesmos bytes, em vez de gerar outra remessa
    para os mesmos títulos.
    """

    __tablename__ = 'cnab_remessas'
    __table_args__ = (
        UniqueConstraint('layout', 'sequencial', name='uq_cnab_remessas_layout_sequencial'),
        CheckConstraint("layout IN ('240', '400')", name='ck_cnab_remessas_layout'),
        CheckConstraint("status IN ('gerada', 'descartada')", name='ck_cnab_remessas_status'),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    layout: Mapped[str] = mapped_column(String(3))
    sequencial: Mapped[int] = mapped_column(Integer)
    arquivo_sha256: Mapped[str] = mapped_column(String(64))
    arquivo: Mapped[bytes] = mapped_column(LargeBinary)
    total_titulos: Mapped[int] = mapped_column(Integer)
    valor_total: Mapped[float] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(20), default='gerada', server_default='gerada')
    generated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    descartada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    descarte_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)


class CnabRemessaItem(Base, TimestampMixin):
    """Título incluído numa remessa. ``reservado`` segura a cobrança para o
    canal CNAB: a emissão pela API Ailos recusa, e vice-versa."""

    __tablename__ = 'cnab_remessa_itens'
    __table_args__ = (
        UniqueConstraint('remessa_id', 'billing_id', name='uq_cnab_remessa_itens_billing'),
        CheckConstraint("status IN ('reservado', 'liberado')", name='ck_cnab_remessa_itens_status'),
        # Uma cobrança só pode estar reservada em UMA remessa por vez.
        Index(
            'uq_cnab_remessa_itens_billing_reservado', 'billing_id',
            unique=True,
            postgresql_where=text("status = 'reservado'"),
            sqlite_where=text("status = 'reservado'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    remessa_id: Mapped[int] = mapped_column(ForeignKey('cnab_remessas.id'), index=True)
    billing_id: Mapped[int] = mapped_column(ForeignKey('billings.id'), index=True)
    valor: Mapped[float] = mapped_column(Numeric(10, 2))
    vencimento: Mapped[date | None] = mapped_column(Date, nullable=True)
    nosso_numero: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='reservado', server_default='reservado')
