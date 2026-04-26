from datetime import date, datetime
from decimal import Decimal
from typing import Optional
import uuid

from sqlalchemy import Enum as SaEnum
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import PaymentMethod


def _payment_method_values(enum_class: type[PaymentMethod]) -> list[str]:
    return [method.value for method in enum_class]


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
    created_at: datetime
    voided_at: Optional[datetime] = None
    voided_reason: Optional[str] = None
    voided_by: Optional[uuid.UUID] = None
    replaces_payment_id: Optional[uuid.UUID] = None
    edited_at: Optional[datetime] = None


class PaymentMetadataUpdate(SQLModel):
    """In-place edit of descriptive fields. None = leave unchanged."""

    payment_date: Optional[date] = None
    payment_method: Optional[PaymentMethod] = None
    reference: Optional[str] = None


class PaymentAmountUpdate(SQLModel):
    """Amount-edit triggers void+replace under the hood."""

    amount: Decimal = Field(decimal_places=2, max_digits=12)
    reason: Optional[str] = None


class PaymentMoveRequest(SQLModel):
    """Move payment from one invoice to another (same customer scope)."""

    invoice_id: uuid.UUID
    reason: Optional[str] = None


class PaymentVoidRequest(SQLModel):
    reason: Optional[str] = None


class Payment(PaymentBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "payments"

    invoice_id: uuid.UUID = Field(foreign_key="invoices.id", index=True)
    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    payment_method: PaymentMethod = Field(
        sa_type=SaEnum(
            PaymentMethod,
            name="payment_method",
            create_constraint=False,
            values_callable=_payment_method_values,
        ),
    )

    # Soft-void state. voided_at IS NULL means the payment is active and
    # counts toward invoice balance / customer outstanding / revenue.
    voided_at: Optional[datetime] = Field(default=None, nullable=True, index=True)
    voided_reason: Optional[str] = Field(default=None, nullable=True)
    voided_by: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id", nullable=True)

    # Lineage. When an amount-edit voids the original and creates a new
    # payment, the new row's replaces_payment_id points back to the voided one.
    replaces_payment_id: Optional[uuid.UUID] = Field(
        default=None, foreign_key="payments.id", nullable=True, index=True
    )

    # Last in-place metadata change (date/method/reference). Null until edited.
    edited_at: Optional[datetime] = Field(default=None, nullable=True)
