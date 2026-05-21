from datetime import datetime
from pydantic import BaseModel


class AuditLogOut(BaseModel):
    id: int
    user_id: int | None = None
    user_name: str | None = None
    user_role: str | None = None
    method: str
    path: str
    entity_type: str | None = None
    entity_id: int | None = None
    status_code: int | None = None
    ip_address: str | None = None
    description: str | None = None
    created_at: datetime | None = None

    model_config = {'from_attributes': True}
