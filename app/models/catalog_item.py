from decimal import Decimal
from typing import Optional
import uuid

from sqlalchemy import Enum as SaEnum, Index
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin
from app.models.enums import CatalogItemUnit


def _catalog_item_unit_values(enum_class: type[CatalogItemUnit]) -> list[str]:
    return [unit.value for unit in enum_class]


class CatalogItemBase(SQLModel):
    name: str = Field(max_length=200)
    description: Optional[str] = Field(default=None, max_length=500)
    unit: CatalogItemUnit = CatalogItemUnit.PIECE
    custom_unit: Optional[str] = Field(default=None, max_length=50)
    default_rate: Decimal = Field(decimal_places=2, max_digits=12)
    gst_percent: Decimal = Field(default=Decimal("18.00"), decimal_places=2, max_digits=5)


class CatalogItemCreate(SQLModel):
    name: str = Field(max_length=200)
    description: Optional[str] = Field(default=None, max_length=500)
    unit: CatalogItemUnit = CatalogItemUnit.PIECE
    custom_unit: Optional[str] = Field(default=None, max_length=50)
    default_rate: Decimal = Field(decimal_places=2, max_digits=12)
    gst_percent: Decimal = Field(default=Decimal("18.00"), decimal_places=2, max_digits=5)


class CatalogItemUpdate(SQLModel):
    name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=500)
    unit: Optional[CatalogItemUnit] = None
    custom_unit: Optional[str] = Field(default=None, max_length=50)
    default_rate: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=12)
    gst_percent: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=5)
    is_active: Optional[bool] = None


class CatalogItemRead(CatalogItemBase):
    id: uuid.UUID
    business_id: uuid.UUID
    is_active: bool
    created_at: CreatedAtMixin.__annotations__["created_at"]
    updated_at: UpdatedAtMixin.__annotations__["updated_at"]


class CatalogItem(CatalogItemBase, UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, table=True):
    __tablename__ = "catalog_items"
    __table_args__ = (
        Index("ix_catalog_items_business_id", "business_id"),
        Index("ix_catalog_items_business_name", "business_id", "name"),
    )

    business_id: uuid.UUID = Field(foreign_key="businesses.id", nullable=False)
    unit: CatalogItemUnit = Field(
        default=CatalogItemUnit.PIECE,
        sa_type=SaEnum(
            CatalogItemUnit,
            name="catalog_item_unit",
            create_constraint=False,
            values_callable=_catalog_item_unit_values,
        ),
    )
    is_active: bool = Field(default=True, nullable=False)
