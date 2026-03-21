from pydantic import BaseModel, field_validator

from app.models.enums import VehicleStatus


class VehicleBase(BaseModel):
    plate: str
    chassis: str | None = None
    renavam: str | None = None
    brand: str | None = None
    model: str | None = None
    year: int | None = None
    color: str | None = None
    type: str | None = None
    status: VehicleStatus = VehicleStatus.ACTIVE
    client_id: int

    @field_validator('plate')
    @classmethod
    def normalize_plate(cls, value: str) -> str:
        value = value.strip().upper().replace('-', '').replace(' ', '')
        if len(value) not in (7, 8):
            raise ValueError('Placa inválida')
        return value

    @field_validator('chassis')
    @classmethod
    def normalize_chassis(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        value = value.strip().upper().replace(' ', '')
        if len(value) < 8:
            raise ValueError('Chassi inválido')
        return value

    @field_validator('renavam')
    @classmethod
    def normalize_renavam(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        digits = ''.join(filter(str.isdigit, value))
        if len(digits) not in (9, 10, 11):
            raise ValueError('RENAVAM inválido')
        return digits

    @field_validator('type')
    @classmethod
    def normalize_type(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        return value.strip().lower()

    @field_validator('year')
    @classmethod
    def validate_year(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 1950 or value > 2100:
            raise ValueError('Ano inválido')
        return value


class VehicleCreate(VehicleBase):
    pass


class VehicleUpdate(BaseModel):
    plate: str | None = None
    chassis: str | None = None
    renavam: str | None = None
    brand: str | None = None
    model: str | None = None
    year: int | None = None
    color: str | None = None
    type: str | None = None
    status: VehicleStatus | None = None
    client_id: int | None = None

    @field_validator('plate')
    @classmethod
    def normalize_plate(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        value = value.strip().upper().replace('-', '').replace(' ', '')
        if len(value) not in (7, 8):
            raise ValueError('Placa inválida')
        return value

    @field_validator('chassis')
    @classmethod
    def normalize_chassis(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        value = value.strip().upper().replace(' ', '')
        if len(value) < 8:
            raise ValueError('Chassi inválido')
        return value

    @field_validator('renavam')
    @classmethod
    def normalize_renavam(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        digits = ''.join(filter(str.isdigit, value))
        if len(digits) not in (9, 10, 11):
            raise ValueError('RENAVAM inválido')
        return digits

    @field_validator('type')
    @classmethod
    def normalize_type(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        return value.strip().lower()

    @field_validator('year')
    @classmethod
    def validate_year(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 1950 or value > 2100:
            raise ValueError('Ano inválido')
        return value


class VehicleOut(VehicleBase):
    id: int

    model_config = {'from_attributes': True}
