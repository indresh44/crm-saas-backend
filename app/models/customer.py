from typing import Optional
import uuid

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin


class CustomerFields(SQLModel):
    """Fields supplied on create; business_id is injected from current user."""

    name: str
    phone: str
    email: Optional[str] = None
    notes: Optional[str] = None


class CustomerBase(CustomerFields):
    business_id: uuid.UUID


class CustomerCreate(CustomerFields):
    pass


class CustomerRead(CustomerBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Customer(CustomerBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "customers"
    __table_args__ = (Index("ix_customers_business_phone", "business_id", "phone"),)

    business_id: uuid.UUID = Field(foreign_key="businesses.id")
