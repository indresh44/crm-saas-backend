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
    description: str
    quantity: Decimal = Field(default=Decimal("1"), decimal_places=2, max_digits=12)
    unit_price: Decimal = Field(decimal_places=2, max_digits=12)
    gst_percent: Decimal = Field(default=Decimal("0.0"), decimal_places=2, max_digits=5)

    def build_model(self, invoice_id: uuid.UUID) -> "InvoiceItem":
        if self.gst_percent not in ALLOWED_GST_PERCENTS:
            raise ValueError("gst_percent must be one of 0, 5, 12, 18, 28")
        return InvoiceItem(
            invoice_id=invoice_id,
            description=self.description,
            quantity=self.quantity,
            unit_price=self.unit_price,
            gst_percent=self.gst_percent,
            amount=self.quantity * self.unit_price,
        )


class InvoiceItemRead(SQLModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    description: str
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
    description: str
    quantity: Decimal = Field(default=Decimal("1"), decimal_places=2, max_digits=12)
    unit_price: Decimal = Field(decimal_places=2, max_digits=12)
    gst_percent: Decimal = Field(default=Decimal("0.0"), decimal_places=2, max_digits=5)
    amount: Decimal = Field(decimal_places=2, max_digits=12)

    invoice: "Invoice" = Relationship(back_populates="items")
