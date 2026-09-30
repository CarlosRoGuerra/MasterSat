from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class PasswordResetToken(Base, TimestampMixin):
    """Pedido de redefinição de senha.

    O token só existe em texto puro no e-mail enviado ao usuário; aqui fica o
    SHA-256 (``token_hash``). Quem lê o banco — um backup, um dump de suporte —
    não consegue usar um pedido pendente. ``token`` é legado: vazio em todo
    pedido novo, mantido só para o rollback do schema (migration d9e4f1a7b2c5).
    """

    __tablename__ = 'password_reset_tokens'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    token: Mapped[str | None] = mapped_column(String(120), unique=True, index=True, nullable=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Entrega do e-mail: sent_at preenchido = SMTP aceitou a mensagem.
    # delivery_error guarda só a categoria do erro (nunca o token/link).
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default='0', nullable=False)
    delivery_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
