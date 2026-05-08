from datetime import datetime
from pydantic import BaseModel


class ManufacturerOut(BaseModel):
    code: str
    description: str


class IntegrationCallResult(BaseModel):
    operation: str
    operation_label: str | None = None
    transaction_id: str | None = None
    status_code: str | None = None
    status_description: str | None = None
    success: bool
    response_payload: dict | list | None = None
    friendly_title: str | None = None
    friendly_message: str | None = None
    technical_message: str | None = None
    retry_allowed: bool = False
    level: str | None = None


class IntegrationFlowOut(BaseModel):
    provider: str = 'multiportal'
    entity_type: str
    entity_id: int | None = None
    overall_success: bool
    steps: list[IntegrationCallResult]


class IntegrationLogOut(BaseModel):
    id: int
    provider: str
    batch_id: str | None = None
    entity_type: str
    entity_id: int | None = None
    operation: str
    operation_label: str | None = None
    transaction_id: str | None = None
    success: bool
    response_code: str | None = None
    response_description: str | None = None
    friendly_title: str | None = None
    friendly_message: str | None = None
    technical_message: str | None = None
    retry_allowed: bool = False
    level: str | None = None
    created_at: datetime | None = None


class IntegrationStatusOut(BaseModel):
    enabled: bool
    wsdl_url: str | None = None
    credentials_configured: bool
    group_codes_configured: bool
