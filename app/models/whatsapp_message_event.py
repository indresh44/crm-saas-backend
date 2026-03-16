from datetime import datetime
from typing import Any, Optional
import uuid

from sqlalchemy import Enum as SaEnum, Index
from sqlmodel import Field, JSON, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import WhatsAppMessageEventType


def _event_type_values(enum_class: type[WhatsAppMessageEventType]) -> list[str]:
    return [e.value for e in enum_class]


class WhatsAppMessageEventBase(SQLModel):
    business_id: uuid.UUID
    whatsapp_message_id_ref: uuid.UUID
    event_type: WhatsAppMessageEventType
    payload: Optional[dict[str, Any]] = None
    event_at: datetime


class WhatsAppMessageEventRead(WhatsAppMessageEventBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class WhatsAppMessageEvent(
    WhatsAppMessageEventBase,
    UUIDPrimaryKeyMixin,
    CreatedAtMixin,
    table=True,
):
    __tablename__ = "whatsapp_message_events"
    __table_args__ = (
        Index(
            "ix_whatsapp_message_events_message_event_at",
            "whatsapp_message_id_ref",
            "event_at",
        ),
    )

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    whatsapp_message_id_ref: uuid.UUID = Field(
        foreign_key="whatsapp_messages.id",
        index=True,
    )
    event_type: WhatsAppMessageEventType = Field(
        sa_type=SaEnum(
            WhatsAppMessageEventType,
            name="whatsapp_message_event_type",
            create_constraint=False,
            values_callable=lambda cls: _event_type_values(cls),
        ),
    )
    payload: Optional[dict[str, Any]] = Field(default=None, sa_type=JSON)
