from datetime import datetime
from typing import Any, Optional
import uuid

from sqlalchemy import Enum as SaEnum, Index
from sqlmodel import Field, JSON, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin
from app.models.enums import (
    WhatsAppMessageDirection,
    WhatsAppMessageStatus,
    WhatsAppMessageType,
)


def _enum_values(enum_class: type) -> list[str]:
    return [e.value for e in enum_class]


class WhatsAppMessageBase(SQLModel):
    business_id: uuid.UUID
    conversation_id: uuid.UUID
    whatsapp_account_id: uuid.UUID
    customer_id: Optional[uuid.UUID] = None
    lead_id: Optional[uuid.UUID] = None
    whatsapp_message_id: Optional[str] = None
    direction: WhatsAppMessageDirection
    message_type: WhatsAppMessageType
    text_body: Optional[str] = None
    media_url: Optional[str] = None
    mime_type: Optional[str] = None
    file_name: Optional[str] = None
    caption: Optional[str] = None
    status: WhatsAppMessageStatus
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    raw_payload: Optional[dict[str, Any]] = None


class WhatsAppMessageRead(WhatsAppMessageBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]
    updated_at: UpdatedAtMixin.__annotations__["updated_at"]


class WhatsAppMessageSendText(SQLModel):
    """Request body for sending a text message."""

    conversation_id: uuid.UUID
    text: str


class WhatsAppMessageSendDocument(SQLModel):
    """Request body for sending a document message."""

    conversation_id: uuid.UUID
    document_url: str
    file_name: str
    caption: Optional[str] = None


class WhatsAppMessage(
    WhatsAppMessageBase,
    UUIDPrimaryKeyMixin,
    CreatedAtMixin,
    UpdatedAtMixin,
    table=True,
):
    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        Index("ix_whatsapp_messages_conversation_created", "conversation_id", "created_at"),
        Index("ix_whatsapp_messages_business_created", "business_id", "created_at"),
        Index("ix_whatsapp_messages_whatsapp_message_id", "whatsapp_message_id"),
    )

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    conversation_id: uuid.UUID = Field(foreign_key="whatsapp_conversations.id", index=True)
    whatsapp_account_id: uuid.UUID = Field(foreign_key="whatsapp_accounts.id", index=True)
    customer_id: Optional[uuid.UUID] = Field(default=None, foreign_key="customers.id")
    lead_id: Optional[uuid.UUID] = Field(default=None, foreign_key="leads.id")
    direction: WhatsAppMessageDirection = Field(
        sa_type=SaEnum(
            WhatsAppMessageDirection,
            name="whatsapp_message_direction",
            create_constraint=False,
            values_callable=lambda cls: _enum_values(cls),
        ),
    )
    message_type: WhatsAppMessageType = Field(
        sa_type=SaEnum(
            WhatsAppMessageType,
            name="whatsapp_message_type",
            create_constraint=False,
            values_callable=lambda cls: _enum_values(cls),
        ),
    )
    status: WhatsAppMessageStatus = Field(
        sa_type=SaEnum(
            WhatsAppMessageStatus,
            name="whatsapp_message_status",
            create_constraint=False,
            values_callable=lambda cls: _enum_values(cls),
        ),
    )
    raw_payload: Optional[dict[str, Any]] = Field(default=None, sa_type=JSON)
