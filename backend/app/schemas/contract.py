from datetime import date
from pydantic import BaseModel, Field


class ContractBase(BaseModel):
    client_id: int
    plan_id: int
    vehicle_id: int | None = None
    tracker_id: int | None = None
    start_date: date
    end_date: date | None = None
    status: str = 'ativo'
    billing_day: int | None = Field(default=None, ge=1, le=28)
    payment_method: str | None = None
    notes: str | None = None
    installation_fee: float | None = None
    uninstall_fee: float | None = None
    delivery_method: str | None = None


class ContractCreate(ContractBase):
    pass


class ContractUpdate(BaseModel):
    client_id: int | None = None
    plan_id: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: str | None = None
    billing_day: int | None = Field(default=None, ge=1, le=28)
    payment_method: str | None = None
    notes: str | None = None
    installation_fee: float | None = None
    uninstall_fee: float | None = None
    delivery_method: str | None = None


class ContractOut(ContractBase):
    id: int
    client_name: str | None = None
    vehicle_id: int | None = None
    tracker_id: int | None = None
    vehicle_plate: str | None = None
    tracker_identifier: str | None = None
    plan_name: str | None = None
    monthly_value: float | None = None
    open_billings: int = 0
    next_due_date: date | None = None

    model_config = {'from_attributes': True}
