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

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.models.customer import Customer
from app.models.enums import ActorType, LeadActivityType
from app.models.lead import Lead, LeadActivity


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

    IMPORTANT (0045): unlike the commit-per-call chokepoint, this helper
    does NOT fire the per-enquiry activity_summary trigger — it can't,
    because it doesn't own the commit. Callers that use this for
    multi-write atomic flows must call
    `enquiry_intelligence_service.fire_rebuild_activity_summary(lead_id)`
    once AFTER their session.commit() (one rebuild covers all rows
    written in the transaction). The incremental path is wrong for
    multi-write flows — it can only fold one row, leaving the others
    unrepresented in the rolling summary.
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


def list_for_business_today(
    session: Session,
    *,
    business_id: UUID,
    start_at: datetime,
    end_at: datetime,
    include_types: Sequence[LeadActivityType],
    hard_cap: int = 500,
) -> list[tuple[LeadActivity, str | None, str | None]]:
    """Today's owner-logged activity for a whole business, newest-first.

    Backs the dashboard "Today's Activity" feed. Unlike `list_for_lead`
    (the per-lead timeline, which deliberately avoids a JOIN to honour the
    lead-scoped index), this is a cross-lead read that genuinely needs the
    lead title + customer name, so it JOINs `Lead` (+ `Customer`). The
    result set is small — one business's human actions in a single day —
    so the JOIN is cheap and `hard_cap` only guards a pathological day.

    Filters:
      * business scope via the Lead join
      * `created_at` inside the caller-supplied UTC day bounds
      * `actor_type = HUMAN` — owner actions only. AI / TASK actions live in
        the assistant "Recently done" section; SYSTEM rows are machine noise.
        (Pre-0042 NULL actor_type rows never fall in a "today" window.)
      * `type IN include_types` — the curated, high-signal set

    Returns (activity, lead_title, customer_name) tuples.
    """
    statement = (
        select(LeadActivity, Lead.title, Customer.name)
        .join(Lead, Lead.id == LeadActivity.lead_id)
        .outerjoin(Customer, Lead.customer_id == Customer.id)
        .where(
            Lead.business_id == business_id,
            LeadActivity.created_at >= start_at,
            LeadActivity.created_at < end_at,
            # LeadActivity.actor_type == ActorType.HUMAN,
            LeadActivity.type.in_(list(include_types)),
        )
        .order_by(LeadActivity.created_at.desc())
        .limit(hard_cap)
    )
    return [tuple(row) for row in session.exec(statement).all()]


# Retry outcomes that feed the negative-attempt tally. Kept as plain strings
# (not the Outcome enum) to avoid a circular import with the service layer —
# matches `lead_followup_service._RETRY`.
_RETRY_OUTCOMES: tuple[str, ...] = ("no_answer", "busy", "wa_not_replied")


def negative_attempt_breakdown(
    session: Session, followup_id: UUID
) -> dict[str, int]:
    """Per-type tally of the CURRENT consecutive retry-negative streak, scoped
    to a single follow-up.

    Walks the call/whatsapp activities tied to THIS follow-up (via
    `lead_activities.followup_id`) newest-first and counts retry outcomes
    (no_answer / busy / wa_not_replied) until the first non-retry outcome — a
    positive / awaiting / terminal resolve writes a non-retry row that ends the
    streak (and completes the follow-up, so a fresh cycle starts on the next
    follow-up). Returns `{no_answer, busy, wa_not_replied, total}`.
    """
    breakdown = {key: 0 for key in _RETRY_OUTCOMES}
    statement = (
        select(LeadActivity)
        .where(
            LeadActivity.followup_id == followup_id,
            LeadActivity.type.in_(
                [LeadActivityType.CALL, LeadActivityType.WHATSAPP]
            ),
        )
        .order_by(LeadActivity.created_at.desc())
    )
    for activity in session.exec(statement):
        outcome = (activity.payload or {}).get("outcome")
        if outcome in breakdown:
            breakdown[outcome] += 1
        else:
            break  # first non-retry contact ends the streak
    breakdown["total"] = sum(breakdown[key] for key in _RETRY_OUTCOMES)
    return breakdown
