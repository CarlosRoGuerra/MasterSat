import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from app.models.enums import VehicleStatus

def _plate_valida(value: str) -> bool:
    """7 caracteres: só o tamanho, como sempre foi aqui — NÃO aplica a regex
    oficial (Mercosul/antiga) que o importador do SGR usa. Essa regex é uma
    guarda de QUALIDADE para dado que vem de fora (rejeitar 'ALESSANDRO',
    'CASE580H'); aplicá-la aqui apertaria retroativamente o cadastro manual e
    quebraria a listagem de qualquer placa de 7 caracteres já existente fora
    do padrão oficial (achado testando: 'ABCD123'/'EFGH567' já cadastrados).

    5-6 caracteres: NOVO — identificador livre de máquina sem placa oficial
    (ex.: 'VIO17', 'MAQ002'), desde que tenha letra E dígito — barra nome de
    pessoa digitado por engano (ex.: 'GEISON')."""
    if len(value) == 7:
        return True
    if len(value) in (5, 6):
        return (
            bool(re.fullmatch(r'[A-Z0-9]+', value))
            and any(c.isalpha() for c in value)
            and any(c.isdigit() for c in value)
        )
    return False


def identifier_is_valid(value: str, is_non_road_asset: bool = False) -> bool:
    if is_non_road_asset:
        return bool(re.fullmatch(r'[A-Z0-9]{1,40}', value))
    return _plate_valida(value)


class VehicleClientChange(BaseModel):
    client_id: int = Field(ge=1)
    # Ausente preserva o pagador; null faz o próprio cliente pagar.
    interveniente_client_id: int | None = Field(default=None, ge=1)
    expected_client_id: int | None = Field(default=None, ge=1)


class VehicleDeleteBatch(BaseModel):
    ids: list[Annotated[int, Field(ge=1)]] = Field(min_length=1, max_length=2000)
    simular: bool = False


class VehicleDeleteBatchItem(BaseModel):
    vehicle_id: int
    plate: str | None = None
    situacao: Literal['aplicado', 'ignorado']
    motivo: str | None = None


class VehicleDeleteBatchOut(BaseModel):
    simulacao: bool
    total_enviados: int
    aplicados: int
    ignorados: int
    itens: list[VehicleDeleteBatchItem]


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
    is_non_road_asset: bool = False
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
    def normalize_plate(cls, value: str, info: ValidationInfo) -> str:
        value = value.strip().upper().replace('-', '').replace(' ', '')
        if not identifier_is_valid(value, info.data.get('is_non_road_asset', False)):
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
    is_non_road_asset: bool = False
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
    def normalize_plate(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None or value == '':
            return None
        value = value.strip().upper().replace('-', '').replace(' ', '')
        if not identifier_is_valid(value, info.data.get('is_non_road_asset', False)):
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
