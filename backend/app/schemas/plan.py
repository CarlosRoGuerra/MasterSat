from pydantic import BaseModel
class PlanBase(BaseModel): name: str; price: float; description: str|None=None; active: bool=True
class PlanCreate(PlanBase): pass
class PlanUpdate(BaseModel): name: str|None=None; price: float|None=None; description: str|None=None; active: bool|None=None
class PlanOut(PlanBase): id: int; model_config={'from_attributes': True}
