from datetime import datetime
import re
from typing import Optional
import uuid

from pydantic import field_validator
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin

GSTIN_REGEX = re.compile(r"^[0-9A-Z]{15}$")


class BusinessBase(SQLModel):
    name: str
    phone: str
    whatsapp_number: Optional[str] = None
    invoice_sequence: int = Field(default=0, nullable=False)
    email: Optional[str] = Field(default=None, max_length=320, nullable=True)
    address: Optional[str] = Field(default=None, max_length=500, nullable=True)
    city: Optional[str] = Field(default=None, max_length=100, nullable=True)
    state: Optional[str] = Field(default=None, max_length=100, nullable=True)
    pin_code: Optional[str] = Field(default=None, max_length=10, nullable=True)
    logo_url: Optional[str] = Field(default=None, max_length=500, nullable=True)
    gst_number: Optional[str] = Field(default=None, max_length=15, nullable=True)
    gst_mode: Optional[str] = Field(default="exclusive", max_length=20, nullable=True)
    invoice_prefix: Optional[str] = Field(default="INV", max_length=10, nullable=True)
    default_due_days: Optional[int] = Field(default=15, nullable=True)
    bank_name: Optional[str] = Field(default=None, max_length=200, nullable=True)
    bank_account_number: Optional[str] = Field(default=None, max_length=50, nullable=True)
    bank_ifsc: Optional[str] = Field(default=None, max_length=20, nullable=True)
    upi_id: Optional[str] = Field(default=None, max_length=100, nullable=True)
    invoice_notes: Optional[str] = Field(default=None, max_length=500, nullable=True)
    invoice_footer: Optional[str] = Field(default=None, max_length=500, nullable=True)


class BusinessCreate(BusinessBase):
    owner_user_id: Optional[uuid.UUID] = None


class BusinessRead(BusinessBase):
    id: uuid.UUID
    owner_user_id: Optional[uuid.UUID]
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Business(BusinessBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "businesses"

    invoice_sequence: int = Field(default=0, nullable=False)
    owner_user_id: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id")


class BusinessSettingsUpdate(SQLModel):
    """Schema for updating business profile + invoice settings. All fields optional."""

    name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    pin_code: str | None = None
    gst_number: str | None = None
    gst_mode: str | None = None
    invoice_prefix: str | None = None
    default_due_days: int | None = None
    bank_name: str | None = None
    bank_account_number: str | None = None
    bank_ifsc: str | None = None
    upi_id: str | None = None
    invoice_notes: str | None = None
    invoice_footer: str | None = None

    @field_validator("gst_number")
    @classmethod
    def validate_gst_number(cls, value: str | None) -> str | None:
        if value is None:
            return value

        normalized = value.strip().upper()
        if not normalized:
            return None
        if not GSTIN_REGEX.fullmatch(normalized):
            raise ValueError("GSTIN must be exactly 15 alphanumeric characters")
        return normalized

    @field_validator("gst_mode")
    @classmethod
    def validate_gst_mode(cls, value: str | None) -> str | None:
        if value is None:
            return value

        normalized = value.strip().lower()
        if not normalized:
            return None
        if normalized not in {"inclusive", "exclusive"}:
            raise ValueError("GST mode must be 'inclusive' or 'exclusive'")
        return normalized

    @field_validator("invoice_prefix")
    @classmethod
    def normalize_invoice_prefix(cls, value: str | None) -> str | None:
        if value is None:
            return value

        normalized = value.strip().upper()
        if not normalized:
            return None
        if len(normalized) > 10:
            raise ValueError("Invoice prefix must be 10 characters or less")
        return normalized


class BusinessSettingsRead(SQLModel):
    """Full business settings response."""

    id: uuid.UUID
    name: str
    phone: str | None
    whatsapp_number: str | None
    email: str | None
    address: str | None
    city: str | None
    state: str | None
    pin_code: str | None
    logo_url: str | None
    gst_number: str | None
    gst_mode: str | None
    invoice_prefix: str | None
    invoice_sequence: int
    default_due_days: int | None
    bank_name: str | None
    bank_account_number: str | None
    bank_ifsc: str | None
    upi_id: str | None
    invoice_notes: str | None
    invoice_footer: str | None
    created_at: datetime
