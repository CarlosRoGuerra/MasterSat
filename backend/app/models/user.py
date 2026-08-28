from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import SoftDeleteMixin, TimestampMixin
from app.models.enums import UserRole


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(180))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.ADMIN)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey('clients.id'), nullable=True)
    # Corte de revogacao de sessao. Todo access/refresh token emitido ANTES
    # deste instante e recusado. Sem isto, trocar a senha nao derrubava nada:
    # o refresh token e um JWT stateless de 7 dias, entao quem tivesse roubado
    # um seguia dentro por ate uma semana DEPOIS da troca. Gravado no
    # /reset-password. NULL = nunca revogado.
    tokens_valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index('uq_users_email_lower', func.lower(email), unique=True),
    )
