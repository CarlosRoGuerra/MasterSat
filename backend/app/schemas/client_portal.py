from datetime import date

from pydantic import BaseModel, field_validator

from app.models.enums import BillingStatus, ClientStatus, DocumentReviewStatus, VehicleStatus


class ClientProfileOut(BaseModel):
    id: int
    name: str
    cpf_cnpj: str
    email: str | None = None
    extra_emails: list[str] | None = None
    phone: str | None = None
    zip_code: str | None = None
    address_line: str | None = None
    address_number: str | None = None
    address_complement: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    status: ClientStatus
    type: str


class ClientProfileUpdate(BaseModel):
    email: str | None = None
    extra_emails: list[str] | None = None
    phone: str | None = None
    zip_code: str | None = None
    address_line: str | None = None
    address_number: str | None = None
    address_complement: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None

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
    def normalize_zip(cls, value: str | None) -> str | None:
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


class ClientVehicleOut(BaseModel):
    id: int
    plate: str
    model: str | None = None
    brand: str | None = None
    year: int | None = None
    manufacture_year: int | None = None
    model_year: int | None = None
    status: VehicleStatus
    type: str | None = None
    chassis: str | None = None
    renavam: str | None = None
    color: str | None = None
    contract_number: str | None = None
    fuel_type: str | None = None


class ClientVehicleDocumentOut(BaseModel):
    id: int
    file_name: str
    category: str
    content_type: str
    size_bytes: int
    review_status: DocumentReviewStatus
    review_notes: str | None = None
    url: str
    download_url: str


class ClientBillingOut(BaseModel):
    id: int
    amount: float
    due_date: date
    status: BillingStatus
    payment_date: date | None = None
    payment_method: str | None = None


class ClientDashboardSummaryOut(BaseModel):
    total_vehicles: int
    active_vehicles: int
    pending_billings: int
    overdue_billings: int
    total_open_amount: float


class ClientDashboardOut(BaseModel):
    profile: ClientProfileOut
    summary: ClientDashboardSummaryOut
    vehicles: list[ClientVehicleOut]
    recent_billings: list[ClientBillingOut]
    client_documents: list[ClientVehicleDocumentOut]
