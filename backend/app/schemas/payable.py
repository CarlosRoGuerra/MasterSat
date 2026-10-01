from datetime import date, datetime

from pydantic import BaseModel, Field


class PayableBase(BaseModel):
    description: str
    supplier: str | None = None
    category: str | None = None
    amount: float = Field(gt=0)
    due_date: date
    notes: str | None = None


class PayableCreate(PayableBase):
    pass


class PayableUpdate(BaseModel):
    description: str | None = None
    supplier: str | None = None
    category: str | None = None
    amount: float | None = Field(default=None, gt=0)
    due_date: date | None = None
    notes: str | None = None
    # Vai para o histórico (payable_change_logs) junto com antes/depois.
    justification: str | None = Field(default=None, max_length=500)


class PayablePay(BaseModel):
    payment_date: date
    payment_method: str
    notes: str | None = None


class PayableCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class PayableRefund(BaseModel):
    justificativa: str = Field(min_length=3, max_length=500)


class PayableChangeLogOut(BaseModel):
    id: int
    payable_id: int
    changed_by_user_id: int | None = None
    action: str
    field_name: str | None = None
    previous_value: str | None = None
    new_value: str | None = None
    justification: str | None = None
    created_at: datetime | None = None

    model_config = {'from_attributes': True}


class PayableOut(PayableBase):
    id: int
    status: str
    payment_date: date | None = None
    payment_method: str | None = None
    overdue_days: int = 0

    model_config = {'from_attributes': True}
