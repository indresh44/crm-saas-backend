from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import Column, Enum as SaEnum, Index
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import FollowupStatus


def _followup_status_values(enum_class: type[FollowupStatus]) -> list[str]:
    return [s.value for s in enum_class]


class LeadFollowupBase(SQLModel):
    lead_id: uuid.UUID
    scheduled_at: datetime
    note: Optional[str] = None
    status: FollowupStatus = FollowupStatus.PENDING


class LeadFollowupCreate(SQLModel):
    lead_id: uuid.UUID
    scheduled_at: datetime
    note: Optional[str] = None


class LeadFollowupDone(SQLModel):
    note: Optional[str] = None


class LeadFollowupReschedule(SQLModel):
    scheduled_at: datetime
    note: Optional[str] = None


class LeadFollowupCancel(SQLModel):
    note: Optional[str] = None


class LeadFollowupUpdate(SQLModel):
    scheduled_at: Optional[datetime] = None
    note: Optional[str] = None
    status: Optional[FollowupStatus] = None
    completed_at: Optional[datetime] = None
    last_outcome: Optional[str] = None
    attempt_count: Optional[int] = None


class LeadFollowupRead(LeadFollowupBase):
    id: uuid.UUID
    created_by: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]
    completed_at: Optional[datetime] = None
    attempt_count: int = 0
    last_outcome: Optional[str] = None
    # Derived (not a column): per-type tally of the current consecutive
    # retry-negative streak — {no_answer, busy, wa_not_replied, total}.
    # Populated by the service / dashboard layer; None when not computed.
    negative_attempts: Optional[dict[str, int]] = None


class LeadFollowupTodayRead(LeadFollowupRead):
    lead_title: Optional[str] = None


class LeadFollowup(
    LeadFollowupBase,
    UUIDPrimaryKeyMixin,
    CreatedAtMixin,
    table=True,
):
    __tablename__ = "lead_followups"
    __table_args__ = (
        Index("ix_lead_followups_lead_status", "lead_id", "status"),
    )

    lead_id: uuid.UUID = Field(foreign_key="leads.id", index=True)
    created_by: uuid.UUID = Field(foreign_key="users.id", index=True)
    status: FollowupStatus = Field(
        default=FollowupStatus.PENDING,
        sa_column=Column(
            SaEnum(
                FollowupStatus,
                name="followup_status",
                create_constraint=False,
                values_callable=_followup_status_values,
            ),
            nullable=False,
            server_default="pending",
        ),
    )
    completed_at: Optional[datetime] = Field(default=None, nullable=True)
    # 0044 — outcome tracking. attempt_count is bumped each time a
    # call/whatsapp outcome is logged against this follow-up; last_outcome
    # mirrors the most recent payload.outcome for quick list rendering
    # without a join into lead_activities.
    attempt_count: int = Field(default=0, nullable=False, sa_column_kwargs={"server_default": "0"})
    last_outcome: Optional[str] = Field(default=None, nullable=True)
