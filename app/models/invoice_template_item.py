from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, Optional
import uuid

from pydantic import field_serializer
from sqlalchemy import CheckConstraint, Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.invoice_item import ALLOWED_GST_PERCENTS

if TYPE_CHECKING:
    from app.models.invoice_template import InvoiceTemplate


class InvoiceTemplateItemCreate(SQLModel):
    name: str = ""
    description: str
    unit: str = "piece"
    catalog_item_id: uuid.UUID | None = None
    quantity: Decimal = Field(default=Decimal("1"), decimal_places=2, max_digits=12)
    unit_price: Decimal = Field(decimal_places=2, max_digits=12)
    gst_percent: Decimal = Field(default=Decimal("0.0"), decimal_places=2, max_digits=5)
    sac_code: str | None = None
    deliverables: Optional[list[str]] = None
    sort_order: int = 0

    def validate_gst(self) -> None:
        if self.gst_percent not in ALLOWED_GST_PERCENTS:
            raise ValueError("gst_percent must be one of 0, 5, 12, 18, 28")


class InvoiceTemplateItemRead(SQLModel):
    id: uuid.UUID
    template_id: uuid.UUID
    name: str
    description: str
    unit: str
    catalog_item_id: uuid.UUID | None
    quantity: Decimal
    unit_price: Decimal
    gst_percent: Decimal
    amount: Decimal
    sac_code: str | None
    deliverables: Optional[list[str]] = None
    sort_order: int
    created_at: CreatedAtMixin.__annotations__["created_at"]

    @field_serializer("quantity", "unit_price", "gst_percent", "amount", when_used="json")
    def serialize_decimal_fields(self, value: Decimal) -> float:
        return float(value)


class InvoiceTemplateItem(UUIDPrimaryKeyMixin, CreatedAtMixin, SQLModel, table=True):
    __tablename__ = "invoice_template_items"
    __table_args__: ClassVar[tuple[CheckConstraint]] = (
        CheckConstraint(
            "gst_percent IN (0, 5, 12, 18, 28)",
            name="ck_invoice_template_items_gst_percent",
        ),
    )

    template_id: uuid.UUID = Field(
        foreign_key="invoice_templates.id",
        nullable=False,
        index=True,
    )
    name: str = Field(default="", max_length=200)
    description: str
    unit: str = Field(default="piece", max_length=50)
    catalog_item_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="catalog_items.id",
        nullable=True,
        index=True,
    )
    quantity: Decimal = Field(default=Decimal("1"), decimal_places=2, max_digits=12)
    unit_price: Decimal = Field(decimal_places=2, max_digits=12)
    gst_percent: Decimal = Field(default=Decimal("0.0"), decimal_places=2, max_digits=5)
    amount: Decimal = Field(decimal_places=2, max_digits=12)
    sac_code: str | None = Field(default=None, max_length=20, nullable=True)
    deliverables: Optional[list[str]] = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    sort_order: int = Field(default=0, nullable=False)

    template: "InvoiceTemplate" = Relationship(back_populates="items")
