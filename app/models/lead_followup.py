from datetime import datetime
from typing import Optional
import uuid

from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin


class LeadFollowupBase(SQLModel):
    lead_id: uuid.UUID
    scheduled_at: datetime
    note: Optional[str] = None
    status: str = "pending"


class LeadFollowupCreate(SQLModel):
    lead_id: uuid.UUID
    scheduled_at: datetime
    note: Optional[str] = None


class LeadFollowupDone(SQLModel):
    note: Optional[str] = None


class LeadFollowupRead(LeadFollowupBase):
    id: uuid.UUID
    created_by: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]
    completed_at: Optional[datetime] = None


class LeadFollowup(
    LeadFollowupBase,
    UUIDPrimaryKeyMixin,
    CreatedAtMixin,
    table=True,
):
    __tablename__ = "lead_followups"

    lead_id: uuid.UUID = Field(foreign_key="leads.id", index=True)
    created_by: uuid.UUID = Field(foreign_key="users.id", index=True)
    completed_at: Optional[datetime] = Field(default=None, nullable=True)
