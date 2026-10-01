from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin

# Tipos de ajuste (FIN-06). Toda diferença entre o valor do título e o valor
# recebido vira um destes, com justificativa e responsável:
#   desconto          — abatimento concedido: recebeu menos e quitou
#   saldo_transferido — recebeu menos; o resto virou outra cobrança
#   encargos          — recebeu mais por juros/multa de atraso
#   credito           — recebeu mais sem ser encargo: fica a favor do cliente
#   estorno           — pagamento desfeito (a cobrança volta a ficar em aberto)
ADJUSTMENT_KINDS = ('desconto', 'saldo_transferido', 'encargos', 'credito', 'estorno')
_KINDS_SQL = ', '.join(f"'{kind}'" for kind in ADJUSTMENT_KINDS)


class BillingAdjustment(Base, TimestampMixin):
    """Movimento que explica a diferença entre o título e o que foi recebido.

    Invariante de uma cobrança paga (ajustes com ``reversed_at`` nulo)::

        paid_amount = amount - desconto - saldo_transferido + encargos + credito

    Estorno não apaga nada: grava um ajuste ``estorno`` com os dados do
    pagamento desfeito e marca ``reversed_at`` nos ajustes daquele pagamento.
    """

    __tablename__ = 'billing_adjustments'
    __table_args__ = (
        CheckConstraint(f'kind IN ({_KINDS_SQL})', name='ck_billing_adjustments_kind'),
        CheckConstraint('amount > 0', name='ck_billing_adjustments_amount_positivo'),
        CheckConstraint(
            "(kind = 'saldo_transferido') = (target_billing_id IS NOT NULL)",
            name='ck_billing_adjustments_saldo_com_destino',
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    billing_id: Mapped[int] = mapped_column(ForeignKey('billings.id'), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    justification: Mapped[str] = mapped_column(Text)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    # Cobrança que recebeu o saldo (só em saldo_transferido).
    target_billing_id: Mapped[int | None] = mapped_column(
        ForeignKey('billings.id', name='fk_billing_adjustments_target'), nullable=True, index=True,
    )
    # Estorno: data/forma/recibo do pagamento desfeito, para não sumirem.
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
