from datetime import date
from typing import Optional
import uuid

from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import TaskStatus


class TaskFields(SQLModel):
    """Fields supplied on create; business_id is injected from current user."""

    lead_id: uuid.UUID
    title: str
    status: TaskStatus = TaskStatus.PENDING
    assigned_to: Optional[uuid.UUID] = None
    due_date: Optional[date] = None


class TaskBase(TaskFields):
    business_id: uuid.UUID


class TaskCreate(TaskFields):
    pass


class TaskRead(TaskBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Task(TaskBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "tasks"

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    lead_id: uuid.UUID = Field(foreign_key="leads.id", index=True)
    assigned_to: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id")
