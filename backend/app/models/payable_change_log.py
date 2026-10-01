from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class PayableChangeLog(Base, TimestampMixin):
    """Histórico de contas a pagar (FIN-08): quem mudou o quê, antes/depois.

    ``action``: criacao, edicao, pagamento, cancelamento, estorno, exclusao.
    Em ``edicao`` há uma linha por campo alterado.
    """

    __tablename__ = 'payable_change_logs'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    payable_id: Mapped[int] = mapped_column(ForeignKey('payables.id'), index=True)
    changed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(20))
    field_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    previous_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
