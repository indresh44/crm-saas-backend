from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import LargeBinary, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin


class WaCredential(
    UUIDPrimaryKeyMixin,
    CreatedAtMixin,
    UpdatedAtMixin,
    SQLModel,
    table=True,
):
    __tablename__ = "wa_credentials"
    __table_args__ = (
        UniqueConstraint("business_id", name="uq_wa_credentials_business_id"),
        UniqueConstraint("phone_number_id", name="uq_wa_credentials_phone_number_id"),
    )

    business_id: uuid.UUID = Field(foreign_key="businesses.id", nullable=False)
    waba_id: str = Field(nullable=False)
    phone_number_id: str = Field(nullable=False, index=True)
    display_phone: str = Field(nullable=False)
    access_token_enc: bytes = Field(sa_type=LargeBinary, nullable=False)
    token_status: str = Field(default="active", nullable=False)
    onboarded_at: Optional[datetime] = Field(default=None, nullable=True)
