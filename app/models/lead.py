from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
import uuid

from sqlalchemy import Column, Enum as SaEnum, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin
from app.models.enums import ActorType, LeadActivityType, LeadSource


def _lead_activity_type_values(enum_class: type[LeadActivityType]) -> list[str]:
    return [activity_type.value for activity_type in enum_class]


def _actor_type_values(enum_class: type[ActorType]) -> list[str]:
    return [a.value for a in enum_class]


def _lead_source_values(enum_class: type[LeadSource]) -> list[str]:
    return [s.value for s in enum_class]


class LeadFields(SQLModel):
    """Fields supplied on create; business_id is injected from current user."""

    customer_id: Optional[uuid.UUID] = None
    stage_id: uuid.UUID
    title: str
    source: Optional[LeadSource] = None
    service_date: Optional[date] = None
    follow_up_at: Optional[datetime] = Field(default=None, nullable=True)
    estimated_value: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=12)
    assigned_to: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class LeadBase(LeadFields):
    business_id: uuid.UUID


class LeadCreate(LeadFields):
    pass


class LeadRead(LeadBase):
    id: uuid.UUID
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    stage_name: Optional[str] = None
    stage_color: Optional[str] = None
    created_at: CreatedAtMixin.__annotations__["created_at"]
    updated_at: UpdatedAtMixin.__annotations__["updated_at"]


class LeadUpdate(SQLModel):
    title: Optional[str] = None
    source: Optional[LeadSource] = None
    service_date: Optional[date] = None
    follow_up_at: Optional[datetime] = Field(default=None, nullable=True)
    estimated_value: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=12)
    assigned_to: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class Lead(LeadBase, UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, table=True):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_business_stage", "business_id", "stage_id"),
        Index("ix_leads_business_created", "business_id", "created_at"),
        Index("ix_leads_business_customer", "business_id", "customer_id"),
    )

    business_id: uuid.UUID = Field(foreign_key="businesses.id")
    customer_id: Optional[uuid.UUID] = Field(default=None, foreign_key="customers.id")
    stage_id: uuid.UUID = Field(foreign_key="pipeline_stages.id")
    assigned_to: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id")
    source: Optional[LeadSource] = Field(
        default=None,
        sa_type=SaEnum(
            LeadSource,
            name="lead_source",
            create_constraint=False,
            values_callable=_lead_source_values,
        ),
        nullable=True,
    )


class LeadActivityFields(SQLModel):
    """Fields supplied on create; lead_id from URL, created_by from current user."""

    type: LeadActivityType
    description: str


class LeadActivityBase(LeadActivityFields):
    lead_id: uuid.UUID
    # NULL-able since 0042 — SYSTEM-actor writes (WhatsApp webhook,
    # auto-recompute jobs) have no real user. Pre-existing rows from
    # before 0042 are non-null; new HUMAN/AI/TASK writes are non-null;
    # only SYSTEM writes legitimately land NULL here. See actor_type.
    created_by: Optional[uuid.UUID] = None


class LeadActivityCreate(LeadActivityFields):
    pass


class LeadActivityUpdate(SQLModel):
    type: Optional[LeadActivityType] = None
    description: Optional[str] = None


class LeadActivityRead(LeadActivityBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]
    # Diary-side fields added in 0042. NULL on pre-enrichment rows; NOT NULL
    # going forward for everything that flows through create_lead_activity
    # (the repository chokepoint stamps actor_type from the ambient context).
    actor_type: Optional[ActorType] = None
    payload: Optional[dict[str, Any]] = None
    chat_session_id: Optional[uuid.UUID] = None
    task_id: Optional[uuid.UUID] = None


class LeadActivity(LeadActivityBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "lead_activities"

    lead_id: uuid.UUID = Field(foreign_key="leads.id", index=True)
    type: LeadActivityType = Field(
        sa_type=SaEnum(
            LeadActivityType,
            name="lead_activity_type",
            create_constraint=False,
            values_callable=_lead_activity_type_values,
        ),
    )
    # NULL since 0042 — see the LeadActivityBase comment above.
    created_by: Optional[uuid.UUID] = Field(
        default=None, foreign_key="users.id", index=True, nullable=True,
    )

    # Diary fields (0042). actor_type/payload may be NULL on pre-existing
    # rows; for new writes the create_lead_activity chokepoint always
    # stamps actor_type (defaulting to SYSTEM + warning if unset upstream).
    actor_type: Optional[ActorType] = Field(
        default=None,
        sa_type=SaEnum(
            ActorType,
            name="lead_activity_actor_type",
            create_constraint=False,
            values_callable=_actor_type_values,
        ),
        nullable=True,
    )
    payload: Optional[dict[str, Any]] = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    chat_session_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            ForeignKey("agent_chat_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    task_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            ForeignKey("agent_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


class LeadMoveRequest(SQLModel):
    stage_id: uuid.UUID
