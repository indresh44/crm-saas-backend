import uuid

from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import AttachmentEntityType


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
    entity_id: uuid.UUID = Field(index=True)
