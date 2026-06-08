"""Data-access for agent_tasks.

Tenant-scoped on every read/write. The runner uses `lock_for_update` while
flipping a task's status (queued -> running -> terminal) to serialise against
a concurrent /confirm targeting the same task. The /confirm path locks first
via `get_by_pending_action_id` (single-row by partial unique index) and then
operates inside the same transaction.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.agent_task import AgentTask, TaskStatus


def create_task(
    session: Session, *,
    business_id: UUID,
    session_id: UUID,
    user_message_id: Optional[UUID],
    batch_id: UUID,
    sequence_index: int,
    description: str,
) -> AgentTask:
    row = AgentTask(
        business_id=business_id,
        session_id=session_id,
        user_message_id=user_message_id,
        batch_id=batch_id,
        sequence_index=sequence_index,
        description=description,
        status=TaskStatus.QUEUED.value,
        continuity_history=[],
    )
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def get_for_update(
    session: Session, *, task_id: UUID, business_id: UUID,
) -> Optional[AgentTask]:
    """Tenant-scoped + row-locked. The runner / confirm path holds this lock
    while it mutates the task's status, continuity_history, or result so two
    concurrent operations against the same task can't interleave."""
    stmt = (
        select(AgentTask)
        .where(AgentTask.id == task_id, AgentTask.business_id == business_id)
        .with_for_update()
    )
    return session.exec(stmt).first()


def get_by_pending_action_id(
    session: Session, *, prepared_action_id: str, business_id: UUID,
) -> Optional[AgentTask]:
    """Single-row lookup guaranteed by the partial UNIQUE index on
    (pending_action_id) WHERE pending_action_id IS NOT NULL. Returns None if
    no task is currently awaiting this prepared action. Locks the row for the
    duration of the /confirm transaction."""
    stmt = (
        select(AgentTask)
        .where(
            AgentTask.pending_action_id == prepared_action_id,
            AgentTask.business_id == business_id,
        )
        .with_for_update()
    )
    return session.exec(stmt).first()


def list_by_batch(
    session: Session, *, session_id: UUID, batch_id: UUID,
) -> list[AgentTask]:
    """Render-time fetch: all tasks in a batch, in split order."""
    stmt = (
        select(AgentTask)
        .where(
            AgentTask.session_id == session_id,
            AgentTask.batch_id == batch_id,
        )
        .order_by(AgentTask.sequence_index.asc())
    )
    return list(session.exec(stmt).all())


def latest_unresolved_ask_user_in_session(
    session: Session,
    *,
    chat_session_id: UUID,
    business_id: UUID,
) -> Optional[AgentTask]:
    """Return the most-recent task in this chat session that is parked
    in the 'ask_user' shape — status='awaiting_approval',
    pending_action_id IS NULL — and not yet resolved. Used by the chat
    service to identify which originating task a follow-up message is
    answering, so that task can be moved out of `needs_input` instead of
    being orphaned by a fresh batch.

    HARD tenant filter on business_id (defence in depth beyond the
    session_id scope) — same convention as the rest of the repo. Locks
    the row FOR UPDATE so the chat service can mutate it atomically
    inside the same transaction that runs the new batch."""
    stmt = (
        select(AgentTask)
        .where(
            AgentTask.session_id == chat_session_id,
            AgentTask.business_id == business_id,
            AgentTask.status == TaskStatus.AWAITING_APPROVAL.value,
            AgentTask.pending_action_id.is_(None),
        )
        .order_by(AgentTask.updated_at.desc())
        .limit(1)
        .with_for_update()
    )
    candidate = session.exec(stmt).first()
    # Final shape check on result.kind — defends against a future status
    # column that lands a non-ask_user task in this same predicate
    # (e.g. a different pause type). The dashboard router uses the same
    # belt-and-braces approach.
    if candidate is None:
        return None
    result = candidate.result or {}
    if isinstance(result, dict) and result.get("kind") == "ask_user":
        return candidate
    return None


def save(session: Session, task: AgentTask) -> AgentTask:
    """Persist mutations made to a locked task row. Caller is responsible for
    committing the outer transaction."""
    session.add(task)
    session.flush()
    session.refresh(task)
    return task
