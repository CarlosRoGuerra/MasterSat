from datetime import datetime
from pydantic import BaseModel
from app.models.enums import OrderStatus, OrderType
class ServiceOrderBase(BaseModel): number: str; type: OrderType; status: OrderStatus=OrderStatus.OPEN; client_id: int; vehicle_id: int|None=None; tracker_id: int|None=None; technician_id: int|None=None; scheduled_at: datetime|None=None; executed_at: datetime|None=None; checklist: dict|None=None; observations: str|None=None
class ServiceOrderCreate(ServiceOrderBase): pass
class ServiceOrderUpdate(BaseModel): number: str|None=None; type: OrderType|None=None; status: OrderStatus|None=None; client_id: int|None=None; vehicle_id: int|None=None; tracker_id: int|None=None; technician_id: int|None=None; scheduled_at: datetime|None=None; executed_at: datetime|None=None; checklist: dict|None=None; observations: str|None=None
class ServiceOrderOut(ServiceOrderBase): id: int; model_config={'from_attributes': True}
