from datetime import date, datetime
from pydantic import BaseModel, Field

from app.models.enums import BillingStatus


class BillingBase(BaseModel):
    contract_id: int | None = None
    client_id: int
    item_id: int | None = None
    vehicle_id: int | None = None
    tracker_id: int | None = None
    title: str | None = None
    billing_type: str = 'recorrente'
    installment_number: int | None = None
    installment_total: int | None = None
    amount: float = Field(gt=0)
    due_date: date
    status: BillingStatus = BillingStatus.PENDING
    payment_date: date | None = None
    payment_method: str | None = None
    notes: str | None = None
    paid_amount: float | None = Field(default=None, gt=0)
    receipt_number: str | None = None
    period_label: str | None = None


class BillingCreate(BillingBase):
    pass


class BillingUpdate(BaseModel):
    contract_id: int | None = None
    client_id: int | None = None
    item_id: int | None = None
    vehicle_id: int | None = None
    tracker_id: int | None = None
    title: str | None = None
    billing_type: str | None = None
    installment_number: int | None = None
    installment_total: int | None = None
    amount: float | None = Field(default=None, gt=0)
    due_date: date | None = None
    status: BillingStatus | None = None
    payment_date: date | None = None
    payment_method: str | None = None
    notes: str | None = None
    paid_amount: float | None = Field(default=None, gt=0)
    justification: str | None = None


class BillingReceive(BaseModel):
    paid_amount: float | None = Field(default=None, gt=0)
    payment_date: date
    payment_method: str
    notes: str | None = None


class BillingCancel(BaseModel):
    reason: str


class BillingOut(BillingBase):
    id: int
    client_name: str | None = None
    vehicle_plate: str | None = None
    tracker_identifier: str | None = None
    plan_name: str | None = None
    contract_status: str | None = None
    overdue_days: int = 0

    model_config = {'from_attributes': True}


class BillingChangeLogOut(BaseModel):
    id: int
    billing_id: int
    changed_by_user_id: int | None
    field_name: str
    previous_value: str | None
    new_value: str | None
    justification: str
    created_at: datetime | None = None

    model_config = {'from_attributes': True}


class RevenueReportItem(BaseModel):
    label: str
    total_received: float
    total_billed: float
    total_outstanding: float


class DelinquentClientItem(BaseModel):
    client_id: int
    client_name: str
    total_open: float
    overdue_count: int


class FinancialSummary(BaseModel):
    active_plans: int
    active_contracts: int
    pending_billings: int
    overdue_billings: int
    pending_amount: float
    overdue_amount: float
    paid_this_month: float
