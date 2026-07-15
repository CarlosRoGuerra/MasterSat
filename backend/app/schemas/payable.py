from datetime import date

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


class PayablePay(BaseModel):
    payment_date: date
    payment_method: str
    notes: str | None = None


class PayableOut(PayableBase):
    id: int
    status: str
    payment_date: date | None = None
    payment_method: str | None = None
    overdue_days: int = 0

    model_config = {'from_attributes': True}
