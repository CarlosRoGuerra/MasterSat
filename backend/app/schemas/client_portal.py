from datetime import date

from pydantic import BaseModel

from app.models.enums import BillingStatus, ClientStatus, VehicleStatus


class ClientProfileOut(BaseModel):
    id: int
    name: str
    cpf_cnpj: str
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    state: str | None = None
    status: ClientStatus


class ClientVehicleOut(BaseModel):
    id: int
    plate: str
    model: str | None = None
    brand: str | None = None
    year: int | None = None
    status: VehicleStatus


class ClientBillingOut(BaseModel):
    id: int
    amount: float
    due_date: date
    status: BillingStatus
    payment_date: date | None = None
    payment_method: str | None = None


class ClientDashboardSummaryOut(BaseModel):
    total_vehicles: int
    active_vehicles: int
    pending_billings: int
    overdue_billings: int
    total_open_amount: float


class ClientDashboardOut(BaseModel):
    profile: ClientProfileOut
    summary: ClientDashboardSummaryOut
    vehicles: list[ClientVehicleOut]
    recent_billings: list[ClientBillingOut]
