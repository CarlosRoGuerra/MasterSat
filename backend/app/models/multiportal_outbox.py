from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class MultiportalOutbox(Base, TimestampMixin):
    """Fila durável de sincronizações pendentes com o Multiportal.

    Padrão outbox: a intenção de sincronizar é gravada na MESMA transação que
    alterou o dado. Se o provedor estiver fora do ar, a intenção sobrevive —
    antes o fluxo rodava inteiro dentro da requisição HTTP e, falhando no meio,
    dependia de alguém notar o status vermelho e apertar "reprocessar".

    Uma linha representa um fluxo completo de um rastreador
    (cliente → usuário → veículo → equipamento → vínculos), que é a unidade
    que o Multiportal exige: sincronizar equipamento sem o cliente/veículo
    correspondente não faz sentido para o provedor.
    """

    __tablename__ = 'multiportal_outbox'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    tracker_id: Mapped[int] = mapped_column(ForeignKey('trackers.id'), index=True)
    operation: Mapped[str] = mapped_column(String(40), default='full_sync', index=True)

    # pending → processing → done | failed
    # 'failed' é terminal por esgotamento de tentativas e exige ação humana;
    # a reconciliação NÃO o reenfileira sozinha, para não repetir para sempre
    # um erro de dado (CPF inválido, por exemplo).
    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # Quando esta linha pode ser tentada de novo (backoff exponencial).
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Por que entrou na fila — ajuda a auditar depois ("cliente alterado",
    # "reconciliação", "vínculo criado").
    reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Correlaciona com integration_logs do processamento.
    batch_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
