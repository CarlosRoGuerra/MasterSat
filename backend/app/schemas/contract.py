from datetime import date
from pydantic import BaseModel
class ContractBase(BaseModel): client_id: int; plan_id: int; start_date: date; end_date: date|None=None; status: str='ativo'
class ContractCreate(ContractBase): pass
class ContractUpdate(BaseModel): client_id: int|None=None; plan_id: int|None=None; start_date: date|None=None; end_date: date|None=None; status: str|None=None
class ContractOut(ContractBase): id: int; model_config={'from_attributes': True}
