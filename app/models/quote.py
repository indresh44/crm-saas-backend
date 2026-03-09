from decimal import Decimal
from typing import Optional
import uuid

from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import QuoteStatus


class QuoteBase(SQLModel):
    lead_id: uuid.UUID
    business_id: uuid.UUID
    title: str
    description: Optional[str] = None
    total_amount: Decimal = Field(decimal_places=2, max_digits=12)
    status: QuoteStatus = QuoteStatus.DRAFT


class QuoteCreate(QuoteBase):
    pass


class QuoteRead(QuoteBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Quote(QuoteBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "quotes"

    lead_id: uuid.UUID = Field(foreign_key="leads.id", index=True)
    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)


class QuoteItemBase(SQLModel):
    quote_id: uuid.UUID
    name: str
    quantity: Decimal = Field(decimal_places=2, max_digits=12)
    price: Decimal = Field(decimal_places=2, max_digits=12)
    total: Decimal = Field(decimal_places=2, max_digits=12)


class QuoteItemCreate(QuoteItemBase):
    pass


class QuoteItemRead(QuoteItemBase):
    id: uuid.UUID


class QuoteItem(QuoteItemBase, UUIDPrimaryKeyMixin, table=True):
    __tablename__ = "quote_items"

    quote_id: uuid.UUID = Field(foreign_key="quotes.id", index=True)
