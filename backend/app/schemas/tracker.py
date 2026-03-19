from datetime import date
from pydantic import BaseModel
from app.models.enums import TrackerStatus
class TrackerBase(BaseModel): imei: str; brand: str|None=None; model: str|None=None; status: TrackerStatus=TrackerStatus.STOCK; sim_number: str|None=None; carrier: str|None=None; warranty_until: date|None=None; client_id: int|None=None; vehicle_id: int|None=None
class TrackerCreate(TrackerBase): pass
class TrackerUpdate(BaseModel): imei: str|None=None; brand: str|None=None; model: str|None=None; status: TrackerStatus|None=None; sim_number: str|None=None; carrier: str|None=None; warranty_until: date|None=None; client_id: int|None=None; vehicle_id: int|None=None
class TrackerOut(TrackerBase): id: int; model_config={'from_attributes': True}
