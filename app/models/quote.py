from decimal import Decimal
from typing import List, Optional
import uuid

from sqlalchemy import Enum as SaEnum
from sqlmodel import Field, Relationship, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import QuoteStatus


def _quote_status_values(enum_class: type[QuoteStatus]) -> list[str]:
    return [status.value for status in enum_class]


class QuoteFields(SQLModel):
    """Fields supplied on create; business_id is injected from current user."""

    lead_id: uuid.UUID
    title: str
    description: Optional[str] = None
    total_amount: Decimal = Field(decimal_places=2, max_digits=12)
    status: QuoteStatus = QuoteStatus.DRAFT


class QuoteBase(QuoteFields):
    business_id: uuid.UUID


class QuoteCreate(QuoteFields):
    pass


class QuoteRead(QuoteBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Quote(QuoteBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "quotes"

    lead_id: uuid.UUID = Field(foreign_key="leads.id", index=True)
    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    status: QuoteStatus = Field(
        sa_type=SaEnum(
            QuoteStatus,
            name="quote_status",
            create_constraint=False,
            values_callable=_quote_status_values,
        ),
    )
    items: List["QuoteItem"] = Relationship(back_populates="quote")
