from sqlalchemy import ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class BillingChargeItem(Base, TimestampMixin):
    """Vínculo rastreável entre uma cobrança combinada e seus serviços.

    ``Billing.item_id`` continua atendendo cobranças avulsas com um único item.
    A primeira mensalidade, porém, pode incorporar vários serviços; por isso
    precisa desta associação N:N, com o valor congelado no fechamento.
    """

    __tablename__ = 'billing_charge_items'
    __table_args__ = (
        UniqueConstraint('billing_id', 'item_id', name='uq_billing_charge_item'),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    billing_id: Mapped[int] = mapped_column(ForeignKey('billings.id'), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey('client_charge_items.id'), index=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
