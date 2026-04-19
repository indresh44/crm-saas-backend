from decimal import Decimal
from typing import List, Optional
import uuid

from sqlmodel import Field, Relationship, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin
from app.models.invoice_template_item import (
    InvoiceTemplateItem,
    InvoiceTemplateItemCreate,
    InvoiceTemplateItemRead,
)


class InvoiceTemplateBase(SQLModel):
    name: str = Field(max_length=200)
    subtotal: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12)
    tax_total: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12)
    total_amount: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12)
    source_invoice_id: Optional[uuid.UUID] = None


class InvoiceTemplateCreate(SQLModel):
    name: str = Field(max_length=200)
    items: List[InvoiceTemplateItemCreate] = Field(default_factory=list)


class InvoiceTemplateUpdate(SQLModel):
    name: Optional[str] = Field(default=None, max_length=200)
    items: Optional[List[InvoiceTemplateItemCreate]] = None


class InvoiceTemplateFromInvoice(SQLModel):
    name: str = Field(max_length=200)


class InvoiceTemplateRead(InvoiceTemplateBase):
    id: uuid.UUID
    business_id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]
    updated_at: UpdatedAtMixin.__annotations__["updated_at"]


class InvoiceTemplateListItem(InvoiceTemplateRead):
    items_count: int = 0


class InvoiceTemplateListResponse(SQLModel):
    items: List[InvoiceTemplateListItem]
    total: int


class InvoiceTemplateReadWithItems(InvoiceTemplateRead):
    items: List[InvoiceTemplateItemRead]


class InvoiceTemplate(
    InvoiceTemplateBase,
    UUIDPrimaryKeyMixin,
    CreatedAtMixin,
    UpdatedAtMixin,
    table=True,
):
    __tablename__ = "invoice_templates"

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True, nullable=False)
    source_invoice_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="invoices.id",
        nullable=True,
    )
    name: str = Field(max_length=200, nullable=False)
    subtotal: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12, nullable=False)
    tax_total: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12, nullable=False)
    total_amount: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12, nullable=False)

    items: List[InvoiceTemplateItem] = Relationship(
        back_populates="template",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
