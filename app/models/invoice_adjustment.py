from decimal import Decimal
from typing import Optional
import uuid

from pydantic import field_serializer, field_validator
from sqlalchemy import CheckConstraint, Index
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import InvoiceAdjustmentType


class InvoiceAdjustmentCreate(SQLModel):
    amount: Decimal = Field(decimal_places=2, max_digits=12)
    adjustment_type: InvoiceAdjustmentType
    reason: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def validate_positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("Adjustment amount must be greater than 0")
        return value


class InvoiceAdjustmentRead(SQLModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    amount: Decimal
    adjustment_type: InvoiceAdjustmentType
    reason: Optional[str]
    created_by: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]

    @field_serializer("amount", when_used="json")
    def serialize_amount(self, value: Decimal) -> float:
        return float(value)


class InvoiceAdjustment(
    UUIDPrimaryKeyMixin,
    CreatedAtMixin,
    SQLModel,
    table=True,
):
    __tablename__ = "invoice_adjustments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_invoice_adjustments_amount_positive"),
        CheckConstraint(
            "adjustment_type IN ('discount', 'write_off')",
            name="ck_invoice_adjustments_type",
        ),
        Index("ix_invoice_adjustments_invoice_id", "invoice_id"),
    )

    invoice_id: uuid.UUID = Field(foreign_key="invoices.id", nullable=False)
    amount: Decimal = Field(decimal_places=2, max_digits=12, nullable=False)
    adjustment_type: str = Field(max_length=20, nullable=False)
    reason: Optional[str] = Field(default=None, nullable=True)
    created_by: uuid.UUID = Field(foreign_key="users.id", nullable=False)
