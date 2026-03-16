from datetime import date
from decimal import Decimal
from typing import Optional
import uuid

from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import PaymentMethod


class PaymentFields(SQLModel):
    """Fields supplied on create; business_id is injected from current user."""

    invoice_id: uuid.UUID
    amount: Decimal = Field(decimal_places=2, max_digits=12)
    payment_method: PaymentMethod
    payment_date: date
    reference: Optional[str] = None


class PaymentBase(PaymentFields):
    business_id: uuid.UUID


class PaymentCreate(PaymentFields):
    pass


class PaymentRead(PaymentBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Payment(PaymentBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "payments"

    invoice_id: uuid.UUID = Field(foreign_key="invoices.id", index=True)
    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
