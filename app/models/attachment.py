import uuid

from sqlalchemy import Enum as SaEnum
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import AttachmentEntityType


def _attachment_entity_type_values(enum_class: type[AttachmentEntityType]) -> list[str]:
    return [entity_type.value for entity_type in enum_class]


class AttachmentFields(SQLModel):
    """Fields supplied on create; business_id is injected from current user."""

    entity_type: AttachmentEntityType
    entity_id: uuid.UUID
    filename: str
    file_url: str
    file_size: int


class AttachmentBase(AttachmentFields):
    business_id: uuid.UUID


class AttachmentRead(AttachmentBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Attachment(AttachmentBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "attachments"

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    entity_type: AttachmentEntityType = Field(
        sa_type=SaEnum(
            AttachmentEntityType,
            name="attachment_entity_type",
            create_constraint=False,
            values_callable=_attachment_entity_type_values,
        ),
    )
    entity_id: uuid.UUID = Field(index=True)
    filename: str
    file_size: int
