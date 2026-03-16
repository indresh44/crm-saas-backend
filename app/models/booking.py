from datetime import date
from decimal import Decimal
import uuid

from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import BookingStatus


class BookingFields(SQLModel):
    """Fields supplied on create; business_id is injected from current user."""

    lead_id: uuid.UUID
    quote_id: uuid.UUID
    event_date: date
    total_amount: Decimal = Field(decimal_places=2, max_digits=12)
    status: BookingStatus = BookingStatus.CONFIRMED


class BookingBase(BookingFields):
    business_id: uuid.UUID


class BookingCreate(BookingFields):
    pass


class BookingRead(BookingBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Booking(BookingBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "bookings"

    lead_id: uuid.UUID = Field(foreign_key="leads.id", index=True)
    quote_id: uuid.UUID = Field(foreign_key="quotes.id", index=True)
    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
