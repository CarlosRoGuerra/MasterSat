from datetime import date
from pydantic import BaseModel, Field


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
    active: bool | None = None
    remove_after_payment: bool | None = None
    status: str | None = None


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
