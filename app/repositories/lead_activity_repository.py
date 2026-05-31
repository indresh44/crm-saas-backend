"""Lead-activity write helpers that DO NOT commit.

The legacy `lead_repository.create_lead_activity` is THE chokepoint for
activity inserts (added in 0042 — it stamps actor_type / chat_session_id /
task_id from the ambient `ActorContext`). It commits per call, which is
fine for one-off emits but breaks the atomicity invariant for flows like
`resolve_followup` that need many activity + state writes in one tx.

This module exposes `add(...)` — same actor stamping, no commit. Caller
is responsible for the surrounding `session.commit()`. After flush the
returned LeadActivity has its server-defaulted `id` / `created_at` set.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.models.enums import ActorType, LeadActivityType
from app.models.lead import LeadActivity


def add(
    session: Session,
    *,
    lead_id: UUID,
    type: LeadActivityType,
    description: str,
    followup_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
    actor_type: ActorType | None = None,
    created_by: UUID | None = None,
) -> LeadActivity:
    """Insert one lead_activities row without committing.

    Mirrors the actor-stamping behaviour of
    `lead_repository.create_lead_activity` — see the comment there for
    the contract. `chat_session_id` / `task_id` are stamped from the
    ambient ActorContext; the caller does not pass them.
    """
    # Local import to avoid a circular import at module load time.
    from app.core.actor_context import current_actor, warn_if_default_on_write

    warn_if_default_on_write()
    ctx = current_actor()

    activity = LeadActivity(
        lead_id=lead_id,
        type=type,
        description=description,
        created_by=created_by,
        followup_id=followup_id,
        payload=payload,
        actor_type=actor_type if actor_type is not None else ctx.actor_type,
        chat_session_id=ctx.chat_session_id,
        task_id=ctx.task_id,
    )
    session.add(activity)
    session.flush()
    session.refresh(activity)
    return activity


def list_for_lead(
    session: Session,
    *,
    lead_id: UUID,
    before: datetime | None = None,
    limit: int = 50,
) -> list[LeadActivity]:
    """Cursor-paged timeline read for one lead.

    Backs `GET /leads/{lead_id}/activities`. Newest-first; cursor is the
    `created_at` of the last row the client already has — pass it back as
    `before` to fetch the next page.

    IMPORTANT (timeline-perf invariant): NO JOIN to lead_followups. The
    `followup_title` the frontend needs is snapshotted into
    `lead_activities.payload` by `resolve_followup`, and the diary is
    served straight off the (lead_id, created_at DESC) composite index
    added in migration 0044. Adding a join here would defeat that index
    and re-introduce N+1 patterns the snapshot was designed to avoid.
    """
    statement = (
        select(LeadActivity)
        .where(LeadActivity.lead_id == lead_id)
        .order_by(LeadActivity.created_at.desc())
        .limit(limit)
    )
    if before is not None:
        statement = statement.where(LeadActivity.created_at < before)
    return list(session.exec(statement).all())
