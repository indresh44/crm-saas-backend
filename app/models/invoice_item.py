from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, Optional
import uuid

from pydantic import field_serializer
from sqlalchemy import CheckConstraint, Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.invoice import Invoice


ALLOWED_GST_PERCENTS: set[Decimal] = {
    Decimal("0"),
    Decimal("5"),
    Decimal("12"),
    Decimal("18"),
    Decimal("28"),
}


class InvoiceItemCreate(SQLModel):
    name: str = ""
    description: str = ""
    unit: str = "piece"
    catalog_item_id: uuid.UUID | None = None
    quantity: Decimal = Field(default=Decimal("1"), decimal_places=2, max_digits=12)
    unit_price: Decimal = Field(decimal_places=2, max_digits=12)
    gst_percent: Decimal = Field(default=Decimal("0.0"), decimal_places=2, max_digits=5)
    sac_code: str | None = None
    deliverables: Optional[list[str]] = None

    def build_model(self, invoice_id: uuid.UUID) -> "InvoiceItem":
        if self.gst_percent not in ALLOWED_GST_PERCENTS:
            raise ValueError("gst_percent must be one of 0, 5, 12, 18, 28")
        return InvoiceItem(
            invoice_id=invoice_id,
            name=self.name,
            description=self.description,
            unit=self.unit,
            catalog_item_id=self.catalog_item_id,
            quantity=self.quantity,
            unit_price=self.unit_price,
            gst_percent=self.gst_percent,
            amount=self.quantity * self.unit_price,
            sac_code=self.sac_code,
            deliverables=self.deliverables,
        )


class InvoiceItemRead(SQLModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
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
    created_at: CreatedAtMixin.__annotations__["created_at"]

    @field_serializer("quantity", "unit_price", "gst_percent", "amount", when_used="json")
    def serialize_decimal_fields(self, value: Decimal) -> float:
        return float(value)


class InvoiceItemUpdate(SQLModel):
    """Fields that can be updated on an invoice item after creation."""
    name: Optional[str] = None
    description: Optional[str] = None
    deliverables: Optional[list[str]] = None


class InvoiceItem(UUIDPrimaryKeyMixin, CreatedAtMixin, SQLModel, table=True):
    __tablename__ = "invoice_items"
    __table_args__: ClassVar[tuple[CheckConstraint]] = (
        CheckConstraint("gst_percent IN (0, 5, 12, 18, 28)", name="ck_invoice_items_gst_percent"),
    )

    invoice_id: uuid.UUID = Field(foreign_key="invoices.id", nullable=False, index=True)
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

    invoice: "Invoice" = Relationship(back_populates="items")
