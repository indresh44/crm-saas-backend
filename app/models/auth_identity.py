import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Enum as SaEnum, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin
from app.models.enums import AuthProvider


def _auth_provider_values(enum_class: type[AuthProvider]) -> list[str]:
    return [provider.value for provider in enum_class]


class AuthIdentityRead(SQLModel):
    id: uuid.UUID
    user_id: uuid.UUID
    provider: AuthProvider
    provider_id: str
    provider_email: Optional[str]
    verified: bool
    created_at: datetime


class AuthIdentity(UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, SQLModel, table=True):
    __tablename__ = "auth_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_auth_identity_provider_id"),
        Index("ix_auth_identities_user_id", "user_id"),
        Index("ix_auth_identities_provider_lookup", "provider", "provider_id"),
    )

    user_id: uuid.UUID = Field(foreign_key="users.id", nullable=False)
    provider: AuthProvider = Field(
        sa_type=SaEnum(
            AuthProvider,
            name="auth_provider",
            create_constraint=False,
            values_callable=_auth_provider_values,
        ),
        nullable=False,
    )
    provider_id: str = Field(max_length=320, nullable=False)
    password_hash: Optional[str] = Field(default=None, max_length=255)
    provider_email: Optional[str] = Field(default=None, max_length=320)
    provider_data: Optional[str] = Field(default=None)
    verified: bool = Field(default=False, nullable=False)
