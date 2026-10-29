from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, field_validator


class ClientChargeItemBase(BaseModel):
    client_id: int
    contract_id: int | None = None
    vehicle_id: int | None = None
    tracker_id: int | None = None
    service_product_id: int | None = None
    title: str = Field(min_length=2, max_length=140)
    description: str | None = None
    quantity: int = Field(default=1, ge=1, le=999)
    unit_price: float = Field(gt=0)
    installment_count: int = Field(default=1, ge=1, le=60)
    start_date: date
    date_basis: Literal['service_date', 'due_date'] = 'service_date'
    active: bool = True
    remove_after_payment: bool = False


class ClientChargeItemCreate(ClientChargeItemBase):
    pass


class ClientChargeItemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=140)
    description: str | None = None
    quantity: int | None = Field(default=None, ge=1, le=999)
    unit_price: float | None = Field(default=None, gt=0)
    installment_count: int | None = Field(default=None, ge=1, le=60)
    start_date: date | None = None
    date_basis: Literal['service_date', 'due_date'] | None = None
    active: bool | None = None
    remove_after_payment: bool | None = None
    status: str | None = None

    @field_validator('date_basis')
    @classmethod
    def require_date_basis(cls, value):
        if value is None:
            raise ValueError('Informe se a data representa serviço ou vencimento.')
        return value


class ClientChargeItemOut(ClientChargeItemBase):
    id: int
    total_amount: float
    completed_at: date | None = None
    status: str
    client_name: str | None = None
    vehicle_plate: str | None = None
    tracker_identifier: str | None = None
    service_product_name: str | None = None
    open_installments: int = 0
    billing_ids: list[int] = Field(default_factory=list)

    model_config = {'from_attributes': True}
