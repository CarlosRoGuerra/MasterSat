from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class RefreshToken(Base, TimestampMixin):
    """Estado server-side de cada refresh token emitido (rotação + detecção de reuso).

    'family' agrupa toda a cadeia de rotações de um mesmo login: se um jti já
    substituído (replaced_by_jti preenchido) for apresentado de novo, é reuso
    de um token roubado — a família inteira é revogada (ver token_revogado_ou_reuso
    em security.py). created_at (via TimestampMixin) é o instante de emissão.
    """

    __tablename__ = 'refresh_tokens'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    jti: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    family: Mapped[str] = mapped_column(String(36), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_jti: Mapped[str | None] = mapped_column(String(36), nullable=True)
