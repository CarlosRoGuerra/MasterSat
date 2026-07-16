from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class SystemSetting(Base, TimestampMixin):
    """Configuração editável pelo painel (chave/valor) — ex.: templates das
    mensagens enviadas aos clientes, evitando texto hardcoded no código."""

    __tablename__ = 'system_settings'

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text)
