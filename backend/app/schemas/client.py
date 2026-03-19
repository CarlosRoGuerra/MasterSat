from pydantic import BaseModel, field_validator

from app.models.enums import ClientStatus


class ClientBase(BaseModel):
    name: str
    cpf_cnpj: str
    type: str = 'pf'
    status: ClientStatus = ClientStatus.ACTIVE
    email: str | None = None
    phone: str | None = None
    zip_code: str | None = None
    address_line: str | None = None
    address_number: str | None = None
    address_complement: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    address: str | None = None
    notes: str | None = None

    @field_validator('cpf_cnpj')
    @classmethod
    def normalize_document(cls, value: str) -> str:
        digits = ''.join(filter(str.isdigit, value))
        if len(digits) not in (11, 14):
            raise ValueError('CPF ou CNPJ inválido')
        return digits

    @field_validator('phone')
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        digits = ''.join(filter(str.isdigit, value))
        if len(digits) not in (10, 11):
            raise ValueError('Telefone inválido')
        return digits

    @field_validator('zip_code')
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


class ClientCreate(ClientBase):
    pass


class ClientUpdate(BaseModel):
    name: str | None = None
    cpf_cnpj: str | None = None
    type: str | None = None
    status: ClientStatus | None = None
    email: str | None = None
    phone: str | None = None
    zip_code: str | None = None
    address_line: str | None = None
    address_number: str | None = None
    address_complement: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    address: str | None = None
    notes: str | None = None


class ClientOut(ClientBase):
    id: int

    model_config = {'from_attributes': True}
