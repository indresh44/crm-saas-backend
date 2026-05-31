from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional, Tuple
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlmodel import Session, select

from app.models.enums import FollowupStatus
from app.models.lead import Lead
from app.models.lead_followup import LeadFollowup


def list_recent_followups_for_lead(
    session: Session,
    lead_id: UUID,
    limit: int = 3,
) -> List[LeadFollowup]:
    """Last N follow-ups (any status) for one lead, newest first.

    Newest = MAX(completed_at, scheduled_at) so a recently-done follow-up
    sorts ahead of a future-scheduled-but-pending one, matching the
    "recent history" framing of the dashboard accordion."""
    sort_anchor = func.coalesce(LeadFollowup.completed_at, LeadFollowup.scheduled_at)
    statement = (
        select(LeadFollowup)
        .where(LeadFollowup.lead_id == lead_id)
        .order_by(sort_anchor.desc())
        .limit(limit)
    )
    return list(session.exec(statement).all())


def list_pending_followups_for_lead_ids(
    session: Session,
    lead_ids: Sequence[UUID],
) -> List[LeadFollowup]:
    """All `status='pending'` follow-ups for these leads, ordered by
    scheduled_at ASC. Caller can stream-pick "next pending" per lead
    (first row wins) without N+1.
    Includes already-overdue rows (scheduled_at < now) — the cascade
    distinguishes overdue vs upcoming itself."""
    if not lead_ids:
        return []
    statement = (
        select(LeadFollowup)
        .where(
            LeadFollowup.lead_id.in_(list(lead_ids)),
            LeadFollowup.status == "pending",
        )
        .order_by(LeadFollowup.scheduled_at)
    )
    return list(session.exec(statement).all())


def create_lead_followup(session: Session, followup: LeadFollowup) -> LeadFollowup:
    session.add(followup)
    session.commit()
    session.refresh(followup)
    return followup


def get_lead_followup_by_id(
    session: Session,
    followup_id: UUID,
) -> Optional[LeadFollowup]:
    statement = select(LeadFollowup).where(LeadFollowup.id == followup_id)
    return session.exec(statement).first()


def list_followups_for_lead(session: Session, lead_id: UUID) -> List[LeadFollowup]:
    statement = (
        select(LeadFollowup)
        .where(LeadFollowup.lead_id == lead_id)
        .order_by(LeadFollowup.scheduled_at)
    )
    return list(session.exec(statement).all())


def list_followups_for_datetime_range(
    session: Session,
    start_at: datetime,
    end_at: datetime,
) -> List[LeadFollowup]:
    statement = (
        select(LeadFollowup)
        .where(
            LeadFollowup.scheduled_at >= start_at,
            LeadFollowup.scheduled_at < end_at,
            LeadFollowup.status == "pending",
        )
        .order_by(LeadFollowup.scheduled_at)
    )
    return list(session.exec(statement).all())


def list_followups_before_datetime(
    session: Session,
    before_at: datetime,
) -> List[LeadFollowup]:
    statement = (
        select(LeadFollowup)
        .where(
            LeadFollowup.scheduled_at < before_at,
            LeadFollowup.status == "pending",
        )
        .order_by(LeadFollowup.scheduled_at)
    )
    return list(session.exec(statement).all())


def list_followups_with_filters(
    session: Session,
    *,
    business_id: UUID,
    lead_id: UUID | None = None,
    customer_id: UUID | None = None,
    followup_ids: list[UUID] | None = None,
    status: str | None = None,
    date_value: date | None = None,
    before_date: date | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    older_than_days: int | None = None,
    limit: int = 50,
) -> List[LeadFollowup]:
    statement = select(LeadFollowup).join(Lead, Lead.id == LeadFollowup.lead_id).where(Lead.business_id == business_id)

    if lead_id is not None:
        statement = statement.where(LeadFollowup.lead_id == lead_id)
    if customer_id is not None:
        statement = statement.where(Lead.customer_id == customer_id)
    if followup_ids:
        statement = statement.where(LeadFollowup.id.in_(followup_ids))
    if status:
        statement = statement.where(LeadFollowup.status == status)
    if date_value is not None:
        start_at = datetime.combine(date_value, time.min, tzinfo=timezone.utc)
        end_at = start_at + timedelta(days=1)
        statement = statement.where(
            LeadFollowup.scheduled_at >= start_at,
            LeadFollowup.scheduled_at < end_at,
        )
    if before_date is not None:
        cutoff_at = datetime.combine(before_date, time.min, tzinfo=timezone.utc)
        statement = statement.where(LeadFollowup.scheduled_at < cutoff_at)
    if from_date is not None:
        start_at = datetime.combine(from_date, time.min, tzinfo=timezone.utc)
        statement = statement.where(LeadFollowup.scheduled_at >= start_at)
    if to_date is not None:
        end_at = datetime.combine(to_date, time.min, tzinfo=timezone.utc) + timedelta(days=1)
        statement = statement.where(LeadFollowup.scheduled_at < end_at)
    if older_than_days is not None:
        cutoff_date = datetime.now(timezone.utc).date() - timedelta(days=older_than_days)
        cutoff_at = datetime.combine(cutoff_date, time.min, tzinfo=timezone.utc)
        statement = statement.where(LeadFollowup.scheduled_at < cutoff_at)

    statement = statement.order_by(LeadFollowup.scheduled_at).limit(limit)
    return list(session.exec(statement).all())


def update_lead_followup(session: Session, followup: LeadFollowup) -> LeadFollowup:
    session.add(followup)
    session.commit()
    session.refresh(followup)
    return followup


# ---------------------------------------------------------------------------
# Outcome-flow helpers (no-commit). Designed for `resolve_followup` which
# wraps a multi-row mutation in ONE transaction; each helper just stages
# changes and flushes so the caller's `session.commit()` is atomic.
# ---------------------------------------------------------------------------


def get_with_lead(
    session: Session,
    followup_id: UUID,
    business_id: UUID,
) -> Tuple[LeadFollowup, Lead]:
    """Fetch a follow-up + its lead with a tenant check baked in.

    Raises 404 if the follow-up doesn't exist OR belongs to another
    business. Single query — used by `resolve_followup` as the first
    statement so the rest of the flow can skip re-checking tenancy.
    """
    statement = (
        select(LeadFollowup, Lead)
        .join(Lead, Lead.id == LeadFollowup.lead_id)
        .where(
            LeadFollowup.id == followup_id,
            Lead.business_id == business_id,
        )
    )
    row = session.exec(statement).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Follow-up not found",
        )
    followup, lead = row
    return followup, lead


def complete(
    session: Session,
    followup: LeadFollowup,
    *,
    outcome: str,
) -> LeadFollowup:
    """Mark a follow-up done with the outcome that closed it.

    Does NOT touch attempt_count — completion means contact succeeded
    (or was terminal), so the retry counter freezes at its current value.
    """
    followup.status = FollowupStatus.DONE
    followup.completed_at = datetime.now(timezone.utc)
    followup.last_outcome = outcome
    session.add(followup)
    session.flush()
    return followup


def reschedule(
    session: Session,
    followup: LeadFollowup,
    *,
    new_dt: datetime,
    outcome: str,
) -> LeadFollowup:
    """Move scheduled_at forward, bump attempt_count, stay pending.

    Used for no-contact outcomes (no_answer, busy, wa_no_number). The
    outcome string is stored on `last_outcome` so the dashboard can show
    "last attempt: busy" without joining lead_activities.
    """
    followup.scheduled_at = new_dt
    followup.attempt_count = (followup.attempt_count or 0) + 1
    followup.last_outcome = outcome
    # status stays pending; completed_at stays NULL.
    session.add(followup)
    session.flush()
    return followup


def create_next(
    session: Session,
    *,
    lead_id: UUID,
    scheduled_dt: datetime,
    created_by: UUID,
    followup_type: str | None = None,
    note: str | None = None,
) -> LeadFollowup:
    """Insert a fresh pending follow-up. attempt_count starts at 0.

    `followup_type` has no column today — when supplied, it is prefixed
    into `note` ("call: …") for visibility on the followups list. Stored
    typed in the activity payload by the service caller.
    """
    combined_note = note
    if followup_type:
        prefix = f"[{followup_type}]"
        combined_note = f"{prefix} {note}" if note else prefix

    followup = LeadFollowup(
        lead_id=lead_id,
        scheduled_at=scheduled_dt,
        note=combined_note,
        created_by=created_by,
        status=FollowupStatus.PENDING,
        attempt_count=0,
    )
    session.add(followup)
    session.flush()
    session.refresh(followup)
    return followup


def get_open_for_lead(
    session: Session,
    lead_id: UUID,
    business_id: UUID,
) -> Optional[LeadFollowup]:
    """Return the single pending follow-up for this lead, if any.

    By product rule we keep at most one pending follow-up per lead; if
    multiple exist (legacy data), the soonest-scheduled wins. Tenant
    check via the leads join.
    """
    statement = (
        select(LeadFollowup)
        .join(Lead, Lead.id == LeadFollowup.lead_id)
        .where(
            LeadFollowup.lead_id == lead_id,
            Lead.business_id == business_id,
            LeadFollowup.status == FollowupStatus.PENDING,
        )
        .order_by(LeadFollowup.scheduled_at)
    )
    return session.exec(statement).first()


def get_open_followups_for_lead_ids(
    session: Session,
    lead_ids: list[UUID],
) -> dict[UUID, LeadFollowup]:
    """Bulk-fetch the earliest pending follow-up per lead, keyed by lead_id.

    Used by the dashboard's leads-needing-action endpoint to stitch
    `open_followup` into each item in one query instead of N. Per the
    product rule (≤1 pending follow-up per lead) we sort by scheduled_at
    ASC and pick the first row per lead_id in Python — a SQL DISTINCT ON
    would be ideal but the pool is small (10 lead ids in the typical
    dashboard request) so a single ORDER BY + dedup in Python is simpler
    and avoids a vendor-specific clause.

    Tenant check is the caller's responsibility — the caller already
    resolved these lead_ids from a tenant-scoped query (list_leads).
    """
    if not lead_ids:
        return {}
    statement = (
        select(LeadFollowup)
        .where(
            LeadFollowup.lead_id.in_(lead_ids),
            LeadFollowup.status == FollowupStatus.PENDING,
        )
        .order_by(LeadFollowup.scheduled_at)
    )
    by_lead: dict[UUID, LeadFollowup] = {}
    for row in session.exec(statement).all():
        # First row per lead_id wins — list is ASC by scheduled_at.
        if row.lead_id not in by_lead:
            by_lead[row.lead_id] = row
    return by_lead
