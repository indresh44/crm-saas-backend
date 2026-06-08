"""Agent multi-task model — one row per split task within an owner message.

A user message can carry several independent instructions ("today's follow-ups
+ remind overdue invoices + any new enquiries"). The splitter turns those into
N task rows; the runner walks them sequentially. Each task has its OWN
continuity scope — fresh history; tasks do NOT share continuity with each
other. (Within a task, the loop's Stage-1 in-task memory still works as
already built.)

Tenant-scoped by `business_id`. Same direct-scoping pattern as every other
write-side entity here.

Lifecycle: queued -> running -> {done | failed | awaiting_approval}.
`awaiting_approval` is terminal until /confirm or /cancel on the task fires,
at which point the runner re-invokes run_agent on that task with its updated
continuity_history (which may end the task or stage another prepare — nested
confirm is allowed).
"""

from __future__ import annotations

import enum
import uuid
from typing import Any, Optional

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin


class TaskStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    FAILED = "failed"


# All values that the CHECK constraint and the API must agree on. Hard-coded
# rather than introspected from the enum so a sloppy enum rename doesn't
# silently drop a value the DB still expects.
_STATUS_VALUES = ("queued", "running", "awaiting_approval", "done", "failed")


class AgentTask(UUIDPrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, table=True):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            + ", ".join(f"'{v}'" for v in _STATUS_VALUES)
            + ")",
            name="ck_agent_tasks_status",
        ),
        # Render ordering: list all tasks for a session, grouped by the batch
        # that spawned them, in their split order. Covers the common per-batch
        # lookup the runner does AND the per-session history scan.
        Index(
            "ix_agent_tasks_session_batch_seq",
            "session_id", "batch_id", "sequence_index",
        ),
        # Admin / debug: "what tasks are in flight right now for this tenant"
        # and "any stuck running/awaiting tasks". Partial would be ideal here
        # but Alembic-side we keep it simple — small composite is fine.
        Index(
            "ix_agent_tasks_business_status_updated",
            "business_id", "status", "updated_at",
        ),
        # Single-row lookup by pending_action_id during /confirm. Unique partial
        # so two tasks can never share an awaiting prepare — the index lives in
        # the migration (Alembic create_index with postgresql_where), the
        # constraint is enforced there.
    )

    # Direct tenant scoping. Same shape as AgentChatSession.business_id.
    business_id: uuid.UUID = Field(
        foreign_key="businesses.id", nullable=False, index=True,
    )

    session_id: uuid.UUID = Field(
        sa_column=Column(
            ForeignKey("agent_chat_sessions.id", ondelete="CASCADE"),
            nullable=False, index=False,  # composite index above covers it
        ),
    )

    # The user message that spawned this batch. ON DELETE SET NULL because
    # messages are append-only in practice but the FK shouldn't block deletion
    # if a future cleanup ever prunes them — the task row carries enough
    # context (description, batch_id) to survive on its own.
    user_message_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            ForeignKey("agent_chat_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # All tasks split from one user message share the same batch_id. Used by
    # the chat renderer to group sibling task outcomes into one assistant
    # bubble; also lets the /confirm handler check "are any siblings still
    # awaiting?" cheaply.
    batch_id: uuid.UUID = Field(nullable=False)

    # 0-based ordering within the batch. The runner walks tasks in this order.
    sequence_index: int = Field(nullable=False)

    # Splitter output (or the original message verbatim for single-task batches).
    description: str = Field(sa_column=Column(Text, nullable=False))

    # Lifecycle. Constrained at the DB level (CHECK above); the enum is the
    # Python-side single source of truth for valid values.
    status: str = Field(
        default=TaskStatus.QUEUED.value, max_length=24, nullable=False,
    )

    # AgentRunResult sans `history` — what the renderer keys on for this task's
    # slot: {kind, answer?, preview?, editable_fields?, question?, error?, ...}
    # _safe_jsonify'd at the boundary.
    result: Optional[dict[str, Any]] = Field(
        default=None, sa_column=Column(JSONB, nullable=True),
    )

    # The task's OWN continuity history (serialized TurnRecord tuple, with
    # observation_raw intact for UUID provenance). Fresh per task — tasks do
    # NOT share continuity with each other. Default empty list; only populated
    # after the first run_agent call on this task.
    continuity_history: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default="[]"),
    )

    # Non-null IFF status='awaiting_approval' AND the pause was a prepare (not
    # an ask_user). The /confirm endpoint looks tasks up by this column; a
    # partial UNIQUE index (in the migration) guarantees the lookup is
    # single-row even under concurrent batches.
    pending_action_id: Optional[str] = Field(
        default=None,
        max_length=64,
        sa_column=Column(
            ForeignKey(
                "prepared_actions.id",
                name="fk_agent_tasks_pending_action",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )

    # TokenReport snapshot from the LAST run_agent call on this task (a task
    # may invoke run_agent more than once if it resumes after confirm). For
    # cumulative accounting across resumes, sum across calls before storage;
    # for v1 we keep last-call only.
    tokens: Optional[dict[str, Any]] = Field(
        default=None, sa_column=Column(JSONB, nullable=True),
    )
