from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class AilosIntegration(Base, TimestampMixin):
    """
    Integração singleton com a Ailos (1 cooperado/convênio para todo o sistema).

    Espera-se uma única linha ativa. ``status`` reflete o ciclo de vida da
    autorização do cooperado: pending -> authorized -> expired/revoked/error.
    """

    __tablename__ = 'ailos_integrations'

    id: Mapped[int] = mapped_column(primary_key=True)
    numero_convenio: Mapped[str] = mapped_column(String(20))
    codigo_carteira: Mapped[int] = mapped_column(Integer, default=1)
    cooperativa_codigo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    conta_numero: Mapped[str | None] = mapped_column(String(20), nullable=True)
    conta_digito: Mapped[str | None] = mapped_column(String(4), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    cooperado_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    cooperado_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Tentativas consecutivas de re-login automático headless. Trava de
    # segurança contra o bloqueio por 3 senhas erradas: ao chegar no limite,
    # o re-login automático se desabiliza. Zera quando autoriza com sucesso.
    auto_relogin_failures: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
