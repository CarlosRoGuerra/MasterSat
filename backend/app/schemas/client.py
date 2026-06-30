from datetime import date

from pydantic import BaseModel, Field, field_validator

from app.models.enums import ClientStatus


class ContactItem(BaseModel):
    name: str
    phone: str | None = None
    email: str | None = None
    role: str | None = None


class EmergencyContact(BaseModel):
    name: str | None = None
    phone: str | None = None
    mobile: str | None = None


def normalize_email_list(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    normalized: list[str] = []
    for item in value:
        email = item.strip().lower()
        if not email:
            continue
        if '@' not in email or '.' not in email.split('@')[-1]:
            raise ValueError('Informe e-mails adicionais válidos')
        if email not in normalized:
            normalized.append(email)
    return normalized or None


class ClientBase(BaseModel):
    name: str
    cpf_cnpj: str
    type: str = 'pf'
    status: ClientStatus = ClientStatus.ACTIVE
    email: str | None = None
    extra_emails: list[str] | None = None
    phone: str | None = None
    contacts: list[ContactItem] | None = None
    zip_code: str | None = None
    address_line: str | None = None
    address_number: str | None = None
    address_complement: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    address: str | None = None
    notes: str | None = None
    billing_day: int | None = Field(default=None, ge=1, le=28)
    rg_ie: str | None = None
    birth_date: date | None = None
    emergency_contacts: list[EmergencyContact] | None = None
    boleto_format: str | None = None
    boleto_fee: str | None = None
    issue_invoice: str | None = None
    tributacao: str | None = None
    iss_retido: str | None = None
    optante_simples: str | None = None
    delivery_method: str | None = None

    @field_validator('cpf_cnpj')
    @classmethod
    def normalize_document(cls, value: str) -> str:
        digits = ''.join(filter(str.isdigit, value))
        if len(digits) not in (11, 14):
            raise ValueError('CPF ou CNPJ inválido')
        return digits

    @field_validator('email')
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        email = value.strip().lower()
        if '@' not in email or '.' not in email.split('@')[-1]:
            raise ValueError('Informe um e-mail válido')
        return email

    @field_validator('extra_emails')
    @classmethod
    def normalize_extra_emails(cls, value: list[str] | None) -> list[str] | None:
        return normalize_email_list(value)

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
    extra_emails: list[str] | None = None
    phone: str | None = None
    contacts: list[ContactItem] | None = None
    zip_code: str | None = None
    address_line: str | None = None
    address_number: str | None = None
    address_complement: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    address: str | None = None
    notes: str | None = None
    billing_day: int | None = Field(default=None, ge=1, le=28)
    rg_ie: str | None = None
    birth_date: date | None = None
    emergency_contacts: list[EmergencyContact] | None = None
    boleto_format: str | None = None
    boleto_fee: str | None = None
    issue_invoice: str | None = None
    tributacao: str | None = None
    iss_retido: str | None = None
    optante_simples: str | None = None
    delivery_method: str | None = None

    @field_validator('cpf_cnpj')
    @classmethod
    def normalize_document(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        digits = ''.join(filter(str.isdigit, value))
        if len(digits) not in (11, 14):
            raise ValueError('CPF ou CNPJ inválido')
        return digits

    @field_validator('email')
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        email = value.strip().lower()
        if '@' not in email or '.' not in email.split('@')[-1]:
            raise ValueError('Informe um e-mail válido')
        return email

    @field_validator('extra_emails')
    @classmethod
    def normalize_extra_emails(cls, value: list[str] | None) -> list[str] | None:
        return normalize_email_list(value)

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


class ClientOut(ClientBase):
    id: int

    model_config = {'from_attributes': True}
