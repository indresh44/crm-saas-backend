from datetime import datetime, timezone
import uuid

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CreatedAtMixin(SQLModel):
    created_at: datetime = Field(default_factory=utcnow, nullable=False)


class UpdatedAtMixin(SQLModel):
    updated_at: datetime = Field(
        default_factory=utcnow,
        nullable=False,
        sa_column_kwargs={"onupdate": utcnow},
    )


class UUIDPrimaryKeyMixin(SQLModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
