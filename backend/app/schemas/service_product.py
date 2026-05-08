from pydantic import BaseModel, Field


class ServiceProductBase(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    category: str = Field(default='servico', min_length=2, max_length=40)
    default_price: float = Field(gt=0)
    description: str | None = None
    active: bool = True
    allow_installments: bool = True
    remove_after_payment: bool = False
    auto_add_on_uninstall: bool = False


class ServiceProductCreate(ServiceProductBase):
    pass


class ServiceProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    category: str | None = Field(default=None, min_length=2, max_length=40)
    default_price: float | None = Field(default=None, gt=0)
    description: str | None = None
    active: bool | None = None
    allow_installments: bool | None = None
    remove_after_payment: bool | None = None
    auto_add_on_uninstall: bool | None = None


class ServiceProductOut(ServiceProductBase):
    id: int

    model_config = {'from_attributes': True}
