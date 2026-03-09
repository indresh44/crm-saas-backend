from typing import Optional
import uuid

from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import UserRole


class UserBase(SQLModel):
    business_id: uuid.UUID
    name: str
    email: str
    phone: Optional[str] = None
    role: UserRole


class UserCreate(UserBase):
    pass


class UserRead(UserBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class User(UserBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "users"

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    email: str = Field(index=True)
