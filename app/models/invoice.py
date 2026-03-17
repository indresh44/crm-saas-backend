from datetime import date
from decimal import Decimal
from typing import List, Optional
import uuid

from sqlalchemy import Enum as SaEnum
from sqlmodel import Field, Relationship, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import InvoiceStatus


def _invoice_status_values(enum_class: type[InvoiceStatus]) -> list[str]:
    return [status.value for status in enum_class]


class InvoiceFields(SQLModel):
    """Fields supplied on create; business_id is injected from current user."""

    booking_id: Optional[uuid.UUID] = None
    lead_id: Optional[uuid.UUID] = None
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
    booking_id: Optional[uuid.UUID] = Field(default=None, foreign_key="bookings.id", nullable=True, index=True)
    lead_id: Optional[uuid.UUID] = Field(default=None, foreign_key="leads.id", nullable=True, index=True)
    invoice_number: str = Field(index=True)
    status: InvoiceStatus = Field(
        sa_type=SaEnum(
            InvoiceStatus,
            name="invoice_status",
            create_constraint=False,
            values_callable=_invoice_status_values,
        ),
    )
    items: List["InvoiceItem"] = Relationship(back_populates="invoice")
