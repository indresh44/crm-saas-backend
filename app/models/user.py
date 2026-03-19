from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import Enum as SaEnum
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import UserRole


def _user_role_values(enum_class: type[UserRole]) -> list[str]:
    return [e.value for e in enum_class]


class UserBase(SQLModel):
    business_id: uuid.UUID
    name: str
    email: str = Field(max_length=320, nullable=False)
    phone: Optional[str] = None
    role: UserRole


class UserCreate(UserBase):
    pass


class UserRead(UserBase):
    id: uuid.UUID
    is_active: bool
    last_login_at: Optional[datetime]
    created_at: CreatedAtMixin.__annotations__["created_at"]


class User(UserBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "users"

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    email: str = Field(max_length=320, nullable=False, index=True)
    is_active: bool = Field(default=True, nullable=False)
    last_login_at: Optional[datetime] = Field(default=None, nullable=True)
    role: UserRole = Field(
        sa_type=SaEnum(
            UserRole,
            name="user_role",
            create_constraint=False,
            values_callable=_user_role_values,
        ),
    )
