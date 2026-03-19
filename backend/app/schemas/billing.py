from datetime import date
from pydantic import BaseModel
from app.models.enums import BillingStatus
class BillingBase(BaseModel): contract_id: int|None=None; client_id: int; amount: float; due_date: date; status: BillingStatus=BillingStatus.PENDING; payment_date: date|None=None; payment_method: str|None=None; notes: str|None=None
class BillingCreate(BillingBase): pass
class BillingUpdate(BaseModel): contract_id: int|None=None; client_id: int|None=None; amount: float|None=None; due_date: date|None=None; status: BillingStatus|None=None; payment_date: date|None=None; payment_method: str|None=None; notes: str|None=None
class BillingOut(BillingBase): id: int; model_config={'from_attributes': True}
