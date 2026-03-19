import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin


class RefreshTokenRead(SQLModel):
    """For admin/settings view — 'your active sessions'."""

    id: uuid.UUID
    device_info: Optional[str]
    expires_at: datetime
    is_revoked: bool
    created_at: datetime


class RefreshToken(UUIDPrimaryKeyMixin, CreatedAtMixin, SQLModel, table=True):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_token_hash", "token_hash", unique=True),
    )

    user_id: uuid.UUID = Field(foreign_key="users.id", nullable=False)
    token_hash: str = Field(max_length=255, nullable=False)
    device_info: Optional[str] = Field(default=None, max_length=500)
    expires_at: datetime = Field(nullable=False)
    is_revoked: bool = Field(default=False, nullable=False)
