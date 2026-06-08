from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin


class WaMessage(
    UUIDPrimaryKeyMixin,
    CreatedAtMixin,
    SQLModel,
    table=True,
):
    __tablename__ = "wa_messages"
    __table_args__ = (
        UniqueConstraint("wamid", name="uq_wa_messages_wamid"),
    )

    business_id: uuid.UUID = Field(foreign_key="businesses.id", nullable=False, index=True)
    wamid: str = Field(nullable=False)
    direction: str = Field(nullable=False)  # "inbound" | "outbound"
    lead_id: Optional[uuid.UUID] = Field(
        default=None, foreign_key="leads.id", nullable=True, index=True
    )
    contact_phone: str = Field(nullable=False, index=True)
    msg_type: str = Field(nullable=False)
    body: Optional[str] = Field(default=None, nullable=True)
    media_url: Optional[str] = Field(default=None, nullable=True)
    template_name: Optional[str] = Field(default=None, nullable=True)
    status: str = Field(nullable=False)
    error_detail: Optional[str] = Field(default=None, nullable=True)
    is_owner_echo: bool = Field(default=False, nullable=False)
    wa_timestamp: datetime = Field(nullable=False)
