from decimal import Decimal
from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import Index, Text
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin


class CustomerFields(SQLModel):
    """Fields supplied on create; business_id is injected from current user."""

    name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    email: Optional[str] = None
    notes: Optional[str] = None
    address: Optional[str] = Field(default=None, max_length=500)
    city: Optional[str] = Field(default=None, max_length=100)
    state: Optional[str] = Field(default=None, max_length=100)
    gst_number: Optional[str] = Field(default=None, max_length=15)


class CustomerBase(CustomerFields):
    business_id: uuid.UUID


class CustomerCreateRequest(CustomerFields):
    pass


class CustomerRead(CustomerBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class CustomerSearchResponse(SQLModel):
    id: uuid.UUID
    name: str
    phone: str
    email: Optional[str] = None


class CustomerLookupResponse(SQLModel):
    found: bool
    customer: Optional[CustomerSearchResponse] = None


class CustomerOutstandingResponse(SQLModel):
    total_invoiced: Decimal
    total_paid: Decimal
    outstanding: Decimal
    overdue_invoices: int


class CustomerRecentActivity(SQLModel):
    type: str
    description: str
    date: datetime
    lead_title: Optional[str] = None


class CustomerSummaryResponse(SQLModel):
    customer: CustomerRead
    lifetime_value: Decimal
    total_outstanding: Decimal
    total_leads: int
    active_leads: int
    total_invoices: int
    upcoming_meetings: int
    recent_activities: list[CustomerRecentActivity]


class CustomerUpdate(SQLModel):
    name: Optional[str] = Field(default=None, min_length=1)
    phone: Optional[str] = Field(default=None, min_length=1)
    email: Optional[str] = None
    notes: Optional[str] = None
    address: Optional[str] = Field(default=None, max_length=500)
    city: Optional[str] = Field(default=None, max_length=100)
    state: Optional[str] = Field(default=None, max_length=100)
    gst_number: Optional[str] = Field(default=None, max_length=15)


class Customer(CustomerBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "customers"
    __table_args__ = (
        Index("ix_customers_business_phone_normalized", "business_id", "phone_normalized"),
    )

    business_id: uuid.UUID = Field(foreign_key="businesses.id")
    phone_normalized: str = Field(sa_type=Text, nullable=False)


CustomerCreate = CustomerCreateRequest
