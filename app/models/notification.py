import uuid

from sqlalchemy import Enum as SaEnum
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import AttachmentEntityType


def _notification_entity_type_values(enum_class: type[AttachmentEntityType]) -> list[str]:
    return [entity_type.value for entity_type in enum_class]


class NotificationBase(SQLModel):
    user_id: uuid.UUID
    type: str
    entity_type: AttachmentEntityType
    entity_id: uuid.UUID
    message: str
    is_read: bool = False


class NotificationCreate(NotificationBase):
    pass


class NotificationRead(NotificationBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Notification(NotificationBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "notifications"

    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    entity_type: AttachmentEntityType = Field(
        sa_type=SaEnum(
            AttachmentEntityType,
            name="attachment_entity_type",
            create_constraint=False,
            values_callable=_notification_entity_type_values,
        ),
    )
    entity_id: uuid.UUID = Field(index=True)
