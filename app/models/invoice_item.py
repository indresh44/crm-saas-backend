from decimal import Decimal
from typing import ClassVar
import uuid

from sqlalchemy import CheckConstraint
from sqlmodel import Field, Relationship, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin


ALLOWED_GST_PERCENTS: set[Decimal] = {
    Decimal("0"),
    Decimal("5"),
    Decimal("12"),
    Decimal("18"),
    Decimal("28"),
}


class InvoiceItemCreate(SQLModel):
    name: str = ""
    description: str
    unit: str = "piece"
    catalog_item_id: uuid.UUID | None = None
    quantity: Decimal = Field(default=Decimal("1"), decimal_places=2, max_digits=12)
    unit_price: Decimal = Field(decimal_places=2, max_digits=12)
    gst_percent: Decimal = Field(default=Decimal("0.0"), decimal_places=2, max_digits=5)

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
    created_at: CreatedAtMixin.__annotations__["created_at"]


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

    invoice: "Invoice" = Relationship(back_populates="items")
