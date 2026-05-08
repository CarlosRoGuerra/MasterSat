from datetime import date

from pydantic import BaseModel, field_validator, model_validator

from app.models.enums import VehicleStatus


class VehicleBase(BaseModel):
    model_config = {'protected_namespaces': ()}

    client_id: int
    sales_point: str | None = None
    seller_consultant: str | None = None
    vehicle_classification: str | None = None
    user_alert: str | None = None
    contract_number: str | None = None
    contract_date: date | None = None
    contract_end_date: date | None = None
    address_zip_code: str | None = None
    address_line: str | None = None
    address_number: str | None = None
    address_complement: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    plate: str
    chassis: str | None = None
    renavam: str | None = None
    brand: str | None = None
    model: str | None = None
    year: int | None = None
    manufacture_year: int | None = None
    model_year: int | None = None
    color: str | None = None
    fuel_type: str | None = None
    type: str | None = None
    fipe_code: str | None = None
    fipe_value: float | None = None
    status: VehicleStatus = VehicleStatus.ACTIVE

    @field_validator('plate')
    @classmethod
    def normalize_plate(cls, value: str) -> str:
        value = value.strip().upper().replace('-', '').replace(' ', '')
        if len(value) != 7:
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

    @field_validator('address_zip_code')
    @classmethod
    def normalize_zip_code(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        digits = ''.join(filter(str.isdigit, value))
        if len(digits) != 8:
            raise ValueError('CEP inválido')
        return digits

    @field_validator('state')
    @classmethod
    def normalize_state(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        value = value.strip().upper()
        if len(value) != 2:
            raise ValueError('UF inválida')
        return value

    @field_validator('type', 'fuel_type', 'vehicle_classification', 'sales_point', 'seller_consultant', 'contract_number', 'brand', 'model', 'color', 'fipe_code')
    @classmethod
    def normalize_text_fields(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        return value.strip()

    @field_validator('year', 'manufacture_year', 'model_year')
    @classmethod
    def validate_year(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 1950 or value > 2100:
            raise ValueError('Ano inválido')
        return value

    @field_validator('fipe_value')
    @classmethod
    def validate_fipe_value(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError('Valor FIPE inválido')
        return round(value, 2)

    @model_validator(mode='after')
    def validate_contract_dates(self):
        if self.contract_date and self.contract_end_date and self.contract_end_date < self.contract_date:
            raise ValueError('A data final do contrato não pode ser anterior à data inicial')
        return self


class VehicleCreate(VehicleBase):
    pass


class VehicleUpdate(BaseModel):
    model_config = {'protected_namespaces': ()}

    client_id: int | None = None
    sales_point: str | None = None
    seller_consultant: str | None = None
    vehicle_classification: str | None = None
    user_alert: str | None = None
    contract_number: str | None = None
    contract_date: date | None = None
    contract_end_date: date | None = None
    address_zip_code: str | None = None
    address_line: str | None = None
    address_number: str | None = None
    address_complement: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    plate: str | None = None
    chassis: str | None = None
    renavam: str | None = None
    brand: str | None = None
    model: str | None = None
    year: int | None = None
    manufacture_year: int | None = None
    model_year: int | None = None
    color: str | None = None
    fuel_type: str | None = None
    type: str | None = None
    fipe_code: str | None = None
    fipe_value: float | None = None
    status: VehicleStatus | None = None

    @field_validator('plate')
    @classmethod
    def normalize_plate(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        value = value.strip().upper().replace('-', '').replace(' ', '')
        if len(value) != 7:
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

    @field_validator('address_zip_code')
    @classmethod
    def normalize_zip_code(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        digits = ''.join(filter(str.isdigit, value))
        if len(digits) != 8:
            raise ValueError('CEP inválido')
        return digits

    @field_validator('state')
    @classmethod
    def normalize_state(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        value = value.strip().upper()
        if len(value) != 2:
            raise ValueError('UF inválida')
        return value

    @field_validator('year', 'manufacture_year', 'model_year')
    @classmethod
    def validate_year(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 1950 or value > 2100:
            raise ValueError('Ano inválido')
        return value

    @field_validator('fipe_value')
    @classmethod
    def validate_fipe_value(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError('Valor FIPE inválido')
        return round(value, 2)


class VehicleOut(VehicleBase):
    id: int

    model_config = {'from_attributes': True, 'protected_namespaces': ()}
