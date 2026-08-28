from pydantic import BaseModel, field_validator

from app.models.enums import UserRole


def _normalize_email(value: str) -> str:
    value = value.strip().lower()
    if '@' not in value:
        raise ValueError('Informe um e-mail válido')
    return value


class UserBase(BaseModel):
    name: str
    email: str
    role: UserRole = UserRole.OPERATIONAL
    active: bool = True
    client_id: int | None = None

    @field_validator('email')
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)


class UserCreate(UserBase):
    password: str


class UserUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    role: UserRole | None = None
    active: bool | None = None
    client_id: int | None = None
    password: str | None = None

    @field_validator('email')
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        return _normalize_email(value) if value is not None else None


class UserOut(UserBase):
    id: int

    model_config = {'from_attributes': True}
