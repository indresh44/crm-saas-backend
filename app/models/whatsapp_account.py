from typing import Optional
import uuid

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin


class WhatsAppAccountFields(SQLModel):
    """Config fields for WhatsApp Cloud API account; business_id is injected."""

    meta_app_id: Optional[str] = None
    waba_id: Optional[str] = None
    phone_number_id: str
    display_phone_number: Optional[str] = None
    verified_name: Optional[str] = None
    access_token: str
    token_type: Optional[str] = None
    webhook_verify_token: str
    app_secret: Optional[str] = None
    is_active: bool = True


class WhatsAppAccountCreate(WhatsAppAccountFields):
    """Schema for creating/upserting account; business_id from current user."""

    pass


class WhatsAppAccountBase(WhatsAppAccountFields):
    business_id: uuid.UUID


class WhatsAppAccountRead(SQLModel):
    """Read schema; excludes access_token and app_secret."""

    id: uuid.UUID
    business_id: uuid.UUID
    meta_app_id: Optional[str] = None
    waba_id: Optional[str] = None
    phone_number_id: str
    display_phone_number: Optional[str] = None
    verified_name: Optional[str] = None
    token_type: Optional[str] = None
    is_active: bool = True
    created_at: CreatedAtMixin.__annotations__["created_at"]
    updated_at: UpdatedAtMixin.__annotations__["updated_at"]


class WhatsAppAccount(
    WhatsAppAccountBase,
    UUIDPrimaryKeyMixin,
    CreatedAtMixin,
    UpdatedAtMixin,
    table=True,
):
    __tablename__ = "whatsapp_accounts"
    __table_args__ = (
        UniqueConstraint("phone_number_id", name="uq_whatsapp_accounts_phone_number_id"),
        Index("ix_whatsapp_accounts_business_active", "business_id", "is_active"),
    )

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
