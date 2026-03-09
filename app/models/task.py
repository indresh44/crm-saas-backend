from datetime import date
from typing import Optional
import uuid

from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import TaskStatus


class TaskBase(SQLModel):
    business_id: uuid.UUID
    lead_id: uuid.UUID
    title: str
    status: TaskStatus = TaskStatus.PENDING
    assigned_to: Optional[uuid.UUID] = None
    due_date: Optional[date] = None


class TaskCreate(TaskBase):
    pass


class TaskRead(TaskBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Task(TaskBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "tasks"

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    lead_id: uuid.UUID = Field(foreign_key="leads.id", index=True)
    assigned_to: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id")
