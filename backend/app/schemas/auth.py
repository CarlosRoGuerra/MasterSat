from datetime import datetime

from pydantic import BaseModel, field_validator, model_validator

from app.models.enums import UserRole


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator('email')
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = 'bearer'


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: int
    name: str
    email: str
    role: UserRole
    client_id: int | None = None

    model_config = {'from_attributes': True}


class RegisterClientRequest(BaseModel):
    name: str
    cpf_cnpj: str
    type: str = 'pf'
    email: str
    phone: str
    zip_code: str
    address_line: str
    address_number: str
    address_complement: str | None = None
    neighborhood: str
    city: str
    state: str
    password: str
    password_confirmation: str

    @field_validator('name', 'address_line', 'address_number', 'neighborhood', 'city')
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError('Campo obrigatório')
        return value

    @field_validator('email')
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if '@' not in value or '.' not in value.split('@')[-1]:
            raise ValueError('Informe um e-mail válido')
        return value

    @field_validator('cpf_cnpj')
    @classmethod
    def valid_document(cls, value: str) -> str:
        digits = ''.join(filter(str.isdigit, value))
        if len(digits) not in (11, 14):
            raise ValueError('CPF ou CNPJ inválido')
        return digits

    @field_validator('phone')
    @classmethod
    def valid_phone(cls, value: str) -> str:
        digits = ''.join(filter(str.isdigit, value))
        if len(digits) not in (10, 11):
            raise ValueError('Telefone inválido')
        return digits

    @field_validator('zip_code')
    @classmethod
    def valid_zip_code(cls, value: str) -> str:
        digits = ''.join(filter(str.isdigit, value))
        if len(digits) != 8:
            raise ValueError('CEP inválido')
        return digits

    @field_validator('state')
    @classmethod
    def valid_state(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 2 or not value.isalpha():
            raise ValueError('UF inválida')
        return value

    @field_validator('type')
    @classmethod
    def valid_person_type(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {'pf', 'pj'}:
            raise ValueError('Tipo de pessoa inválido')
        return value

    @field_validator('password')
    @classmethod
    def strong_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError('A senha deve ter ao menos 8 caracteres')
        checks = [
            any(c.islower() for c in value),
            any(c.isupper() for c in value),
            any(c.isdigit() for c in value),
            any(not c.isalnum() for c in value),
        ]
        if not all(checks):
            raise ValueError('A senha deve conter letra maiúscula, minúscula, número e caractere especial')
        return value

    @model_validator(mode='after')
    def passwords_match(self):
        if self.password != self.password_confirmation:
            raise ValueError('As senhas não conferem')
        return self


class RegisterClientResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = 'bearer'
    user: UserOut
    message: str = 'Cadastro realizado com sucesso.'


class ForgotPasswordRequest(BaseModel):
    email: str

    @field_validator('email')
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class ForgotPasswordResponse(BaseModel):
    message: str
    reset_token: str | None = None
    expires_at: datetime | None = None


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str
    password_confirmation: str

    @field_validator('new_password')
    @classmethod
    def strong_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError('A senha deve ter ao menos 8 caracteres')
        checks = [
            any(c.islower() for c in value),
            any(c.isupper() for c in value),
            any(c.isdigit() for c in value),
            any(not c.isalnum() for c in value),
        ]
        if not all(checks):
            raise ValueError('A senha deve conter letra maiúscula, minúscula, número e caractere especial')
        return value

    @model_validator(mode='after')
    def passwords_match(self):
        if self.new_password != self.password_confirmation:
            raise ValueError('As senhas não conferem')
        return self
