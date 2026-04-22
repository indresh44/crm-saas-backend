import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin


class PasswordResetToken(UUIDPrimaryKeyMixin, CreatedAtMixin, SQLModel, table=True):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        Index("ix_password_reset_tokens_user_id", "user_id"),
        Index("ix_password_reset_tokens_token_hash", "token_hash", unique=True),
    )

    user_id: uuid.UUID = Field(foreign_key="users.id", nullable=False)
    token_hash: str = Field(max_length=255, nullable=False)
    expires_at: datetime = Field(nullable=False)
    used_at: Optional[datetime] = Field(default=None, nullable=True)
