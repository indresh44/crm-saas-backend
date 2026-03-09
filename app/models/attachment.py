import uuid

from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import AttachmentEntityType


class AttachmentBase(SQLModel):
    business_id: uuid.UUID
    entity_type: AttachmentEntityType
    entity_id: uuid.UUID
    file_url: str


class AttachmentCreate(AttachmentBase):
    pass


class AttachmentRead(AttachmentBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Attachment(AttachmentBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "attachments"

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    entity_id: uuid.UUID = Field(index=True)
