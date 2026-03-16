from datetime import date
from decimal import Decimal
import uuid

from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import InvoiceStatus


class InvoiceFields(SQLModel):
    """Fields supplied on create; business_id is injected from current user."""

    booking_id: uuid.UUID
    invoice_number: str
    total_amount: Decimal = Field(decimal_places=2, max_digits=12)
    status: InvoiceStatus = InvoiceStatus.DRAFT
    issued_date: date
    due_date: date


class InvoiceBase(InvoiceFields):
    business_id: uuid.UUID


class InvoiceCreate(InvoiceFields):
    pass


class InvoiceRead(InvoiceBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Invoice(InvoiceBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "invoices"

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    booking_id: uuid.UUID = Field(foreign_key="bookings.id", index=True)
    invoice_number: str = Field(index=True)
