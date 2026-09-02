from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.enums import OrderPriority, OrderStatus, OrderType


class ChecklistItem(BaseModel):
    description: str
    done: bool = False
    notes: str | None = None


def _coerce_checklist(value):
    """Tolera o formato antigo (``{"items": ["texto", ...]}``) e listas de
    string soltas, além do novo formato tipado — sem exigir migração de dado
    nas ordens já existentes."""
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get('items') or []
    if not isinstance(value, list):
        return value
    coerced = []
    for entry in value:
        if isinstance(entry, str):
            coerced.append({'description': entry, 'done': False, 'notes': None})
        else:
            coerced.append(entry)
    return coerced


class ServiceOrderMaterialIn(BaseModel):
    service_product_id: int | None = None
    description: str = Field(min_length=1, max_length=300)
    quantity: Decimal = Field(default=Decimal('1'), gt=0)
    unit: str | None = Field(default=None, max_length=10)
    unit_price: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None


class ServiceOrderMaterialOut(ServiceOrderMaterialIn):
    id: int
    service_order_id: int
    service_product_name: str | None = None

    model_config = {'from_attributes': True}


class SignatureIn(BaseModel):
    signer: Literal['technician', 'client']
    # Data URL (``data:image/png;base64,...``) ou base64 puro do PNG
    # capturado no canvas de assinatura do frontend.
    image_base64: str


class ServiceOrderBase(BaseModel):
    type: OrderType
    status: OrderStatus = OrderStatus.OPEN
    priority: OrderPriority = OrderPriority.NORMAL
    client_id: int
    vehicle_id: int | None = None
    tracker_id: int | None = None
    technician_id: int | None = None
    scheduled_at: datetime | None = None
    executed_at: datetime | None = None
    checklist: list[ChecklistItem] | None = None
    observations: str | None = None
    problem_description: str | None = None
    execution_description: str | None = None

    @field_validator('checklist', mode='before')
    @classmethod
    def _validate_checklist(cls, value):
        return _coerce_checklist(value)


class ServiceOrderCreate(ServiceOrderBase):
    number: str | None = Field(default=None, max_length=30)


class ServiceOrderUpdate(BaseModel):
    number: str | None = Field(default=None, max_length=30)
    type: OrderType | None = None
    status: OrderStatus | None = None
    priority: OrderPriority | None = None
    client_id: int | None = None
    vehicle_id: int | None = None
    tracker_id: int | None = None
    technician_id: int | None = None
    scheduled_at: datetime | None = None
    executed_at: datetime | None = None
    checklist: list[ChecklistItem] | None = None
    observations: str | None = None
    problem_description: str | None = None
    execution_description: str | None = None

    @field_validator('checklist', mode='before')
    @classmethod
    def _validate_checklist(cls, value):
        return _coerce_checklist(value)


class ServiceOrderStatusUpdate(BaseModel):
    status: OrderStatus
    notes: str | None = None


class ServiceOrderOut(ServiceOrderBase):
    id: int
    number: str
    client_name: str | None = None
    vehicle_plate: str | None = None
    tracker_label: str | None = None
    technician_name: str | None = None
    technician_signed_at: datetime | None = None
    client_signed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {'from_attributes': True}


class ServiceOrderStatusLogOut(BaseModel):
    id: int
    previous_status: OrderStatus | None = None
    new_status: OrderStatus
    notes: str | None = None
    changed_by_id: int | None = None
    changed_by_name: str | None = None
    created_at: datetime | None = None

    model_config = {'from_attributes': True}


class ServiceOrderPdfCreate(BaseModel):
    kind: str = Field(pattern='^(ordem_servico|termo_instalacao|termo_retirada|historico_execucao)$')
    format: Literal['pdf', 'docx'] = 'pdf'
