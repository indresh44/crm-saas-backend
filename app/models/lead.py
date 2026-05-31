from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
import uuid

from sqlalchemy import Column, Enum as SaEnum, ForeignKey, Index, text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin
from app.models.enums import ActorType, LeadActivityType, LeadSource


class NextActionType(str, Enum):
    """The single derived "what should I do next?" classifier per lead.
    Cascade priority is the declaration order (first match wins)."""

    FOLLOWUP_OVERDUE = "followup_overdue"
    FOLLOWUP_DUE_TODAY = "followup_due_today"
    NO_FOLLOWUP_SET = "no_followup_set"
    GONE_QUIET = "gone_quiet"
    FOLLOWUP_UPCOMING = "followup_upcoming"
    NONE = "none"


# urgency_rank mapping — lower = more urgent. Used as the primary sort key
# across enquiries; secondary sort is `relevant_date ASC NULLS LAST`.
_URGENCY_RANK: dict[NextActionType, int] = {
    NextActionType.FOLLOWUP_OVERDUE: 1,
    NextActionType.FOLLOWUP_DUE_TODAY: 2,
    NextActionType.NO_FOLLOWUP_SET: 3,
    NextActionType.GONE_QUIET: 4,
    NextActionType.FOLLOWUP_UPCOMING: 5,
    NextActionType.NONE: 6,
}


def urgency_rank_for(action_type: NextActionType) -> int:
    return _URGENCY_RANK[action_type]


# The action set that surfaces on the home screen "needs attention" list.
# Cascade types 1-4. UPCOMING and NONE are explicitly excluded.
HOME_NEEDS_ACTION_TYPES: frozenset[NextActionType] = frozenset({
    NextActionType.FOLLOWUP_OVERDUE,
    NextActionType.FOLLOWUP_DUE_TODAY,
    NextActionType.NO_FOLLOWUP_SET,
    NextActionType.GONE_QUIET,
})


class NextActionSummary(SQLModel):
    """Server-derived next action for one lead. The frontend renders this
    verbatim — no business logic in the UI."""

    type: NextActionType
    label: str
    urgency_rank: int
    relevant_date: Optional[datetime] = None


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
    next_action: Optional[NextActionSummary] = None


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
    # 0044 — last_contacted_at is bumped whenever a CALL or WHATSAPP
    # activity lands; powers the GONE_QUIET cascade without scanning
    # lead_activities. phone_flagged is set when an outcome of
    # wrong_number / wa_no_number is logged so the UI can warn the user.
    last_contacted_at: Optional[datetime] = Field(default=None, nullable=True)
    phone_flagged: bool = Field(
        default=False,
        nullable=False,
        sa_column_kwargs={"server_default": sa_text("false")},
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
    followup_id: Optional[uuid.UUID] = None


class LeadActivity(LeadActivityBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "lead_activities"
    __table_args__ = (
        # 0044 — timeline read path is always lead-scoped, newest-first.
        Index(
            "ix_lead_activities_lead_created_desc",
            "lead_id", sa_text("created_at DESC"),
        ),
        Index("ix_lead_activities_followup_id", "followup_id"),
    )

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
    # 0044 — links an activity row to the follow-up it resolved (call/whatsapp
    # outcomes). NULL for everything that isn't a follow-up resolution. ON
    # DELETE SET NULL so deleting a follow-up doesn't blow away its diary.
    followup_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            ForeignKey("lead_followups.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


class LeadMoveRequest(SQLModel):
    stage_id: uuid.UUID


class LeadContextFollowupRead(SQLModel):
    """One follow-up row in the per-lead context bundle. Lighter than
    `LeadFollowupRead` — only the fields the dashboard context accordion
    actually renders."""

    id: uuid.UUID
    scheduled_at: datetime
    note: Optional[str] = None
    status: str  # pending | done | cancelled
    completed_at: Optional[datetime] = None


class LeadContextActivityRead(SQLModel):
    """One human-touch activity row in the context bundle. Mirrors the
    GONE_QUIET cascade's "what counts as a touch" definition — only
    CALL/WHATSAPP/MEETING/NOTE entries by HUMAN/AI actors. System events
    never appear here."""

    id: uuid.UUID
    created_at: datetime
    type: LeadActivityType
    description: str


class LeadContextRead(SQLModel):
    """Per-lead context bundle for the dashboard action-card accordion.

    Lazy-fetched on first expand. Compact-by-design: each list is capped
    at 3 — for "more", users click through to the full enquiry page."""

    enquiry_note: Optional[str] = None
    ai_summary: Optional[str] = None  # placeholder; AI summaries not built yet
    recent_followups: list[LeadContextFollowupRead] = []
    recent_activity: list[LeadContextActivityRead] = []
