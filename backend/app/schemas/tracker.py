from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.models.enums import TrackerStatus


class TrackerBase(BaseModel):
    imei: str
    brand: str
    model: str
    status: TrackerStatus = TrackerStatus.STOCK
    sim_number: str | None = None
    sim_iccid: str | None = None
    carrier: str | None = None
    sim_status: str | None = None
    firmware: str | None = None
    external_manufacturer_id: int | None = None
    external_manufacturer_label: str | None = None
    ip_address: str | None = None
    port: int | None = None
    install_location: str | None = None
    chip_type: int | None = None
    equipment_type: int | None = None
    communication_type: int | None = None
    service_plan_name: str | None = None
    installation_fee: float | None = None
    acquisition_date: date | None = None
    install_date: date | None = None
    warranty_until: date | None = None
    notes: str | None = None
    client_id: int | None = None
    vehicle_id: int | None = None

    @field_validator('imei')
    @classmethod
    def normalize_imei(cls, value: str) -> str:
        digits = ''.join(filter(str.isdigit, value or ''))
        if len(digits) < 5:
            raise ValueError('Número de série / ID inválido')
        return digits

    @field_validator(
        'brand',
        'model',
        'carrier',
        'sim_status',
        'notes',
        'firmware',
        'external_manufacturer_label',
        'ip_address',
        'install_location',
        'service_plan_name',
    )
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator('sim_number', 'sim_iccid')
    @classmethod
    def normalize_digits(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        digits = ''.join(filter(str.isdigit, value))
        return digits or None


class TrackerCreate(TrackerBase):
    pass


class TrackerUpdate(BaseModel):
    imei: str | None = None
    brand: str | None = None
    model: str | None = None
    status: TrackerStatus | None = None
    sim_number: str | None = None
    sim_iccid: str | None = None
    carrier: str | None = None
    sim_status: str | None = None
    firmware: str | None = None
    external_manufacturer_id: int | None = None
    external_manufacturer_label: str | None = None
    ip_address: str | None = None
    port: int | None = None
    install_location: str | None = None
    chip_type: int | None = None
    equipment_type: int | None = None
    communication_type: int | None = None
    service_plan_name: str | None = None
    installation_fee: float | None = None
    acquisition_date: date | None = None
    install_date: date | None = None
    warranty_until: date | None = None
    notes: str | None = None
    client_id: int | None = None
    vehicle_id: int | None = None

    @field_validator('imei')
    @classmethod
    def normalize_imei(cls, value: str | None) -> str | None:
        if value is None:
            return None
        digits = ''.join(filter(str.isdigit, value or ''))
        if len(digits) < 5:
            raise ValueError('Número de série / ID inválido')
        return digits

    @field_validator(
        'brand', 'model', 'carrier', 'sim_status', 'notes',
        'firmware', 'external_manufacturer_label', 'ip_address',
        'install_location', 'service_plan_name',
    )
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator('sim_number', 'sim_iccid')
    @classmethod
    def normalize_digits(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        digits = ''.join(filter(str.isdigit, value))
        return digits or None


class TrackerOut(BaseModel):
    id: int
    imei: str
    brand: str | None = None
    model: str | None = None
    status: TrackerStatus
    sim_number: str | None = None
    sim_iccid: str | None = None
    carrier: str | None = None
    sim_status: str | None = None
    firmware: str | None = None
    external_manufacturer_id: int | None = None
    external_manufacturer_label: str | None = None
    ip_address: str | None = None
    port: int | None = None
    install_location: str | None = None
    chip_type: int | None = None
    equipment_type: int | None = None
    communication_type: int | None = None
    service_plan_name: str | None = None
    installation_fee: float | None = None
    acquisition_date: date | None = None
    install_date: date | None = None
    warranty_until: date | None = None
    notes: str | None = None
    client_id: int | None = None
    vehicle_id: int | None = None
    client_name: str | None = None
    vehicle_plate: str | None = None
    vehicle_model: str | None = None
    active_plan_id: int | None = None
    active_plan_name: str | None = None
    integration_status: str | None = None
    integration_last_code: str | None = None
    integration_last_description: str | None = None
    integration_last_transaction_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {'from_attributes': True}


class TrackerHistoryOut(BaseModel):
    id: int
    tracker_id: int
    action: str
    previous_vehicle_id: int | None = None
    new_vehicle_id: int | None = None
    previous_client_id: int | None = None
    new_client_id: int | None = None
    previous_status: str | None = None
    new_status: str | None = None
    event_date: date | None = None
    notes: str | None = None
    created_by_user_id: int | None = None
    created_at: datetime | None = None

    model_config = {'from_attributes': True}


class TrackerLinkPayload(BaseModel):
    vehicle_id: int
    plan_id: int | None = None
    start_date: date = Field(default_factory=date.today)
    billing_day: int | None = Field(default=None, ge=1, le=28)
    payment_method: str | None = None
    notes: str | None = None
    auto_generate_billings: bool = True
    billing_cycles: int = Field(default=12, ge=1, le=60)
