from pydantic import BaseModel
from app.models.enums import VehicleStatus
class VehicleBase(BaseModel): plate: str; chassis: str|None=None; renavam: str|None=None; brand: str|None=None; model: str|None=None; year: int|None=None; color: str|None=None; type: str|None=None; status: VehicleStatus=VehicleStatus.ACTIVE; client_id: int
class VehicleCreate(VehicleBase): pass
class VehicleUpdate(BaseModel): plate: str|None=None; chassis: str|None=None; renavam: str|None=None; brand: str|None=None; model: str|None=None; year: int|None=None; color: str|None=None; type: str|None=None; status: VehicleStatus|None=None; client_id: int|None=None
class VehicleOut(VehicleBase): id: int; model_config={'from_attributes': True}
