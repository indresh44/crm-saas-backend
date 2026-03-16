from typing import Optional
import uuid

from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import MessageChannel, MessageDirection


class MessageFields(SQLModel):
    """Fields supplied on create; business_id is injected from current user."""

    customer_id: Optional[uuid.UUID] = None
    lead_id: Optional[uuid.UUID] = None
    direction: MessageDirection
    channel: MessageChannel
    message_text: str
    status: str


class MessageBase(MessageFields):
    business_id: uuid.UUID


class MessageCreate(MessageFields):
    pass


class MessageRead(MessageBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Message(MessageBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "messages"

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    customer_id: Optional[uuid.UUID] = Field(default=None, foreign_key="customers.id")
    lead_id: Optional[uuid.UUID] = Field(default=None, foreign_key="leads.id")
