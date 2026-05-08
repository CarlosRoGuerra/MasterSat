from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field

from app.models.enums import OrderStatus, OrderType


class ServiceOrderBase(BaseModel):
    type: OrderType
    status: OrderStatus = OrderStatus.OPEN
    client_id: int
    vehicle_id: int | None = None
    tracker_id: int | None = None
    technician_id: int | None = None
    scheduled_at: datetime | None = None
    executed_at: datetime | None = None
    checklist: dict | None = None
    observations: str | None = None


class ServiceOrderCreate(ServiceOrderBase):
    number: str | None = Field(default=None, max_length=30)


class ServiceOrderUpdate(BaseModel):
    number: str | None = Field(default=None, max_length=30)
    type: OrderType | None = None
    status: OrderStatus | None = None
    client_id: int | None = None
    vehicle_id: int | None = None
    tracker_id: int | None = None
    technician_id: int | None = None
    scheduled_at: datetime | None = None
    executed_at: datetime | None = None
    checklist: dict | None = None
    observations: str | None = None


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
