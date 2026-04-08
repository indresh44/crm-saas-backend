from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
import uuid

from sqlalchemy import Enum as SaEnum
from sqlmodel import Field, Relationship, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import InvoiceStatus
from app.models.invoice_item import InvoiceItem, InvoiceItemRead


def _invoice_status_values(enum_class: type[InvoiceStatus]) -> list[str]:
    return [status.value for status in enum_class]


class InvoiceCreateFields(SQLModel):
    """Fields supplied on create; business_id and invoice_number are server-generated."""

    booking_id: Optional[uuid.UUID] = None
    lead_id: Optional[uuid.UUID] = None
    status: InvoiceStatus = InvoiceStatus.DRAFT
    issued_date: date
    due_date: date


class InvoiceBase(InvoiceCreateFields):
    business_id: uuid.UUID
    quote_id: Optional[uuid.UUID] = None
    subtotal: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12)
    tax_total: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12)
    total_amount: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12)
    invoice_number: str
    pdf_url: Optional[str] = Field(default=None, max_length=500)
    pdf_generated_at: Optional[datetime] = None


class InvoiceCreate(InvoiceCreateFields):
    pass


class InvoiceRead(InvoiceBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class InvoiceReadWithItems(InvoiceRead):
    items: List[InvoiceItemRead]


class InvoiceListItem(InvoiceRead):
    amount_paid: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12)
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    lead_title: Optional[str] = None


class InvoiceListSummary(SQLModel):
    total_outstanding: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12)
    outstanding_count: int = 0


class InvoiceListResponse(SQLModel):
    items: List[InvoiceListItem]
    total: int
    limit: int
    offset: int
    summary: InvoiceListSummary


class InvoicePublicMeta(SQLModel):
    """Minimal invoice info for public (unauthenticated) access."""
    invoice_number: str
    total_amount: Decimal
    due_date: date
    status: InvoiceStatus
    customer_name: Optional[str] = None
    business_name: str = ""
    items_count: int = 0


class Invoice(InvoiceBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "invoices"

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    booking_id: Optional[uuid.UUID] = Field(default=None, foreign_key="bookings.id", nullable=True, index=True)
    lead_id: Optional[uuid.UUID] = Field(default=None, foreign_key="leads.id", nullable=True, index=True)
    quote_id: Optional[uuid.UUID] = Field(default=None, foreign_key="quotes.id", nullable=True, index=True)
    subtotal: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12, nullable=False)
    tax_total: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12, nullable=False)
    total_amount: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12, nullable=False)
    invoice_number: str = Field(index=True)
    status: InvoiceStatus = Field(
        sa_type=SaEnum(
            InvoiceStatus,
            name="invoice_status",
            create_constraint=False,
            values_callable=_invoice_status_values,
        ),
    )
    items: List[InvoiceItem] = Relationship(back_populates="invoice")
