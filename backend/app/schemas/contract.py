from datetime import date
from pydantic import BaseModel, Field


class ContractBase(BaseModel):
    client_id: int
    interveniente_client_id: int | None = None
    plan_id: int
    vehicle_id: int | None = None
    tracker_id: int | None = None
    start_date: date
    end_date: date | None = None
    status: str = 'ativo'
    # 1..31 — normalize_due_date() já reduz para o último dia do mês quando o
    # mês é mais curto (dia 31 em fevereiro vira 28/29), então limitar em 28
    # apenas impedia vencimentos comuns como dia 30.
    billing_day: int | None = Field(default=None, ge=1, le=31)
    payment_method: str | None = None
    bank: str | None = None
    notes: str | None = None
    installation_fee: float | None = None
    uninstall_fee: float | None = None


class ContractCreate(ContractBase):
    pass


class ContractUpdate(BaseModel):
    client_id: int | None = None
    interveniente_client_id: int | None = None
    plan_id: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: str | None = None
    # 1..31 — normalize_due_date() já reduz para o último dia do mês quando o
    # mês é mais curto (dia 31 em fevereiro vira 28/29), então limitar em 28
    # apenas impedia vencimentos comuns como dia 30.
    billing_day: int | None = Field(default=None, ge=1, le=31)
    payment_method: str | None = None
    bank: str | None = None
    notes: str | None = None
    installation_fee: float | None = None
    uninstall_fee: float | None = None


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
