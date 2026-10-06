import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.product import ComponentType, ProductKind


class ProductComponentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    component_type: ComponentType
    quantity: int
    event_id: uuid.UUID | None


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    label: str
    kind: ProductKind
    price_adult: Decimal | None
    price_child: Decimal | None
    price_reduced: Decimal | None
    family_base_price: Decimal | None
    extra_child_price: Decimal | None
    is_addon: bool
    components: list[ProductComponentRead]


class SeasonCheck(BaseModel):
    date: date
    high_season: bool
