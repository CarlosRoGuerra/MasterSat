from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.contract import Contract
from app.models.service_product import ServiceProduct
from app.models.uninstall_event import UninstallEvent
from app.services.billing_closure.shared import _apply_client_scope
from app.services.financial import add_months, contract_payer_client_id


def _pending_uninstall_events_for_month(
    db: Session,
    reference_month: date,
    filter_type: str = 'all',
    client_id: int | None = None,
) -> list[UninstallEvent]:
    """Eventos vencidos até a competência, inclusive os esquecidos em meses anteriores.

    ``skipped`` era o estado terminal usado para valores abaixo de R$ 5. Ele é
    incluído para recuperar dados históricos e volta a ``pending`` enquanto
    aguarda acumulação suficiente.
    """
    if reference_month.month == 12:
        month_end = date(reference_month.year + 1, 1, 1)
    else:
        month_end = date(reference_month.year, reference_month.month + 1, 1)
    query = (
        db.query(UninstallEvent)
        .filter(
            UninstallEvent.status.in_(('pending', 'skipped')),
            UninstallEvent.billing_id.is_(None),
            UninstallEvent.uninstall_date < month_end,
        )
    )
    return _apply_client_scope(
        query, UninstallEvent.client_id, filter_type, client_id,
    ).order_by(UninstallEvent.uninstall_date.asc(), UninstallEvent.id.asc()).all()


def uninstall_fee_for_event(db: Session, event: UninstallEvent) -> tuple[Decimal, str]:
    """Valor e título da taxa de um evento de desinstalação.

    ``fee_amount`` é o valor efetivamente acordado no momento da retirada e
    tem precedência absoluta: o produto de serviço define apenas O QUE foi
    cobrado (título/vínculo com o catálogo), nunca QUANTO.

    Antes as duas coisas eram somadas. Como a tela preenche a taxa direta com
    o preço do produto ao selecioná-lo, escolher um serviço de desinstalação
    cobrava o dobro de forma determinística. Somar também deixava o valor
    refém do catálogo: mudar o preço do produto depois alterava uma cobrança
    já negociada com o cliente.

    O preço do produto só é consultado como fallback, para eventos antigos
    gravados sem ``fee_amount``.
    """
    product = None
    if event.service_product_id:
        candidate = db.get(ServiceProduct, event.service_product_id)
        if candidate and not candidate.is_deleted:
            product = candidate

    if event.fee_amount is not None and Decimal(str(event.fee_amount)) > 0:
        fee_amount = Decimal(str(event.fee_amount))
    elif product is not None:
        fee_amount = Decimal(str(product.default_price))
    else:
        fee_amount = Decimal('0')

    return fee_amount, (product.name if product else 'Taxa de desinstalação')


def _due_date_for_uninstall_event(event: UninstallEvent, db: Session) -> date:
    contract = db.get(Contract, event.contract_id) if event.contract_id else None
    client = db.get(Client, event.client_id)
    billing_day = (
        (contract.billing_day if contract and contract.billing_day else None)
        or (client.billing_day if client and client.billing_day else None)
        or 1
    )
    dim = monthrange(event.uninstall_date.year, event.uninstall_date.month)[1]
    fee_billing_day = min(billing_day, dim)
    due = date(event.uninstall_date.year, event.uninstall_date.month, fee_billing_day)
    if due <= event.uninstall_date:
        due = add_months(due, 1)
    return due


def _uninstall_event_payer_client_id(db: Session, event: UninstallEvent) -> int:
    """Pagador do evento, sem aceitar referência contratual inconsistente."""
    if event.payer_client_id:
        payer = db.get(Client, event.payer_client_id)
        if not payer or payer.is_deleted:
            raise ValueError(
                f'Responsável financeiro #{event.payer_client_id} do evento '
                f'#{event.id} não está disponível.'
            )
        return payer.id
    if not event.contract_id:
        return event.client_id
    contract = db.get(Contract, event.contract_id)
    if not contract or contract.is_deleted:
        raise ValueError(f'Contrato #{event.contract_id} da desinstalação não está disponível.')
    if contract.client_id != event.client_id:
        raise ValueError(
            f'Evento #{event.id} e contrato #{contract.id} pertencem a clientes diferentes.'
        )
    return contract_payer_client_id(db, contract)
