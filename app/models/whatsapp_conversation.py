from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin


class WhatsAppConversationBase(SQLModel):
    business_id: uuid.UUID
    whatsapp_account_id: uuid.UUID
    customer_id: Optional[uuid.UUID] = None
    lead_id: Optional[uuid.UUID] = None
    phone_number: str
    contact_name: Optional[str] = None
    last_message_at: Optional[datetime] = None
    last_incoming_at: Optional[datetime] = None
    last_outgoing_at: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    is_blocked: bool = False


class WhatsAppConversationRead(WhatsAppConversationBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]
    updated_at: UpdatedAtMixin.__annotations__["updated_at"]


class WhatsAppConversationLink(SQLModel):
    """Body for linking a conversation to lead/customer."""

    lead_id: Optional[uuid.UUID] = None
    customer_id: Optional[uuid.UUID] = None


class FindOrCreateConversationByLeadRequest(SQLModel):
    lead_id: uuid.UUID


class WhatsAppConversation(
    WhatsAppConversationBase,
    UUIDPrimaryKeyMixin,
    CreatedAtMixin,
    UpdatedAtMixin,
    table=True,
):
    __tablename__ = "whatsapp_conversations"
    __table_args__ = (
        Index("ix_whatsapp_conversations_business_phone", "business_id", "phone_number"),
        Index("ix_whatsapp_conversations_business_lead", "business_id", "lead_id"),
        Index("ix_whatsapp_conversations_business_customer", "business_id", "customer_id"),
        Index("ix_whatsapp_conversations_business_last_message", "business_id", "last_message_at"),
    )

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    whatsapp_account_id: uuid.UUID = Field(foreign_key="whatsapp_accounts.id", index=True)
    customer_id: Optional[uuid.UUID] = Field(default=None, foreign_key="customers.id")
    lead_id: Optional[uuid.UUID] = Field(default=None, foreign_key="leads.id")
