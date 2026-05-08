from pydantic import BaseModel, Field


class PlanBase(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    price: float = Field(gt=0)
    description: str | None = None
    active: bool = True
    billing_interval_months: int = Field(default=1, ge=1, le=12)


class PlanCreate(PlanBase):
    pass


class PlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    price: float | None = Field(default=None, gt=0)
    description: str | None = None
    active: bool | None = None
    billing_interval_months: int | None = Field(default=None, ge=1, le=12)


class PlanOut(PlanBase):
    id: int
    active_contracts: int = 0

    model_config = {'from_attributes': True}
