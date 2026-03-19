from decimal import Decimal
from typing import List, Optional
import uuid

from sqlalchemy import Enum as SaEnum
from sqlmodel import Field, Relationship, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import QuoteStatus


def _quote_status_values(enum_class: type[QuoteStatus]) -> list[str]:
    return [status.value for status in enum_class]


class QuoteCreateFields(SQLModel):
    """Fields supplied on create; business_id and totals are injected by the backend."""

    lead_id: uuid.UUID
    description: Optional[str] = None
    status: QuoteStatus = QuoteStatus.DRAFT
    is_template: bool = False


class QuoteBase(QuoteCreateFields):
    business_id: uuid.UUID
    subtotal: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12)
    tax_total: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12)
    total_amount: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12)


class QuoteCreate(QuoteCreateFields):
    pass


class QuoteRead(QuoteBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Quote(QuoteBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "quotes"

    lead_id: uuid.UUID = Field(foreign_key="leads.id", index=True)
    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    title: Optional[str] = Field(default=None)
    is_template: bool = Field(default=False, nullable=False)
    subtotal: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12, nullable=False)
    tax_total: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12, nullable=False)
    total_amount: Decimal = Field(default=Decimal("0.00"), decimal_places=2, max_digits=12, nullable=False)
    status: QuoteStatus = Field(
        sa_type=SaEnum(
            QuoteStatus,
            name="quote_status",
            create_constraint=False,
            values_callable=_quote_status_values,
        ),
    )
    items: List["QuoteItem"] = Relationship(back_populates="quote")
