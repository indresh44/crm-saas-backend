from typing import Optional
import uuid

from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin


class BusinessBase(SQLModel):
    name: str
    phone: str
    whatsapp_number: Optional[str] = None


class BusinessCreate(BusinessBase):
    owner_user_id: Optional[uuid.UUID] = None


class BusinessRead(BusinessBase):
    id: uuid.UUID
    owner_user_id: Optional[uuid.UUID]
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Business(BusinessBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "businesses"

    owner_user_id: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id")
