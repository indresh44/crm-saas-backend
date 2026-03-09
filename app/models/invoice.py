from datetime import date
from decimal import Decimal
import uuid

from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import InvoiceStatus


class InvoiceBase(SQLModel):
    business_id: uuid.UUID
    booking_id: uuid.UUID
    invoice_number: str
    total_amount: Decimal = Field(decimal_places=2, max_digits=12)
    status: InvoiceStatus = InvoiceStatus.DRAFT
    issued_date: date
    due_date: date


class InvoiceCreate(InvoiceBase):
    pass


class InvoiceRead(InvoiceBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Invoice(InvoiceBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "invoices"

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    booking_id: uuid.UUID = Field(foreign_key="bookings.id", index=True)
    invoice_number: str = Field(index=True)
