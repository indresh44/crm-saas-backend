from typing import Optional
import uuid

from sqlalchemy import Enum as SaEnum
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import MessageChannel, MessageDirection


def _message_direction_values(enum_class: type[MessageDirection]) -> list[str]:
    return [direction.value for direction in enum_class]


def _message_channel_values(enum_class: type[MessageChannel]) -> list[str]:
    return [channel.value for channel in enum_class]


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
    direction: MessageDirection = Field(
        sa_type=SaEnum(
            MessageDirection,
            name="message_direction",
            create_constraint=False,
            values_callable=_message_direction_values,
        ),
    )
    channel: MessageChannel = Field(
        sa_type=SaEnum(
            MessageChannel,
            name="message_channel",
            create_constraint=False,
            values_callable=_message_channel_values,
        ),
    )
