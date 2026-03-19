from datetime import datetime
import uuid

from sqlalchemy import Enum as SaEnum, Index
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin
from app.models.enums import MeetingStatus


def _meeting_status_values(enum_class: type[MeetingStatus]) -> list[str]:
    return [status.value for status in enum_class]


class MeetingBase(SQLModel):
    title: str = Field(max_length=200)
    customer_id: uuid.UUID
    lead_id: uuid.UUID | None = Field(default=None, nullable=True)
    scheduled_at: datetime
    duration_minutes: int = 30
    notes: str | None = Field(default=None, max_length=1000)


class MeetingCreate(MeetingBase):
    pass


class MeetingUpdate(SQLModel):
    title: str | None = Field(default=None, max_length=200)
    customer_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None
    duration_minutes: int | None = None
    status: MeetingStatus | None = None
    notes: str | None = Field(default=None, max_length=1000)


class MeetingRead(MeetingBase):
    id: uuid.UUID
    business_id: uuid.UUID
    status: MeetingStatus
    gcal_event_id: str | None = None
    created_at: CreatedAtMixin.__annotations__["created_at"]
    updated_at: UpdatedAtMixin.__annotations__["updated_at"]


class Meeting(MeetingBase, UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, table=True):
    __tablename__ = "meetings"
    __table_args__ = (
        Index("ix_meetings_business_customer", "business_id", "customer_id"),
        Index("ix_meetings_business_scheduled", "business_id", "scheduled_at"),
    )

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)
    customer_id: uuid.UUID = Field(foreign_key="customers.id", index=True)
    lead_id: uuid.UUID | None = Field(default=None, foreign_key="leads.id", index=True)
    status: MeetingStatus = Field(
        default=MeetingStatus.SCHEDULED,
        sa_type=SaEnum(
            MeetingStatus,
            name="meeting_status",
            create_constraint=False,
            values_callable=_meeting_status_values,
        ),
    )
    gcal_event_id: str | None = Field(default=None, max_length=255, nullable=True)
