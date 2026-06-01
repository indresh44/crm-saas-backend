from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import BackgroundTasks, HTTPException, status
from sqlmodel import Session, select

from app.models.enums import LeadActivityType
from app.models.lead import LeadActivity, LeadActivityCreate, LeadActivityUpdate
from app.models.user import User
from app.repositories import lead_activity_repository as activity_repo
from app.repositories.lead_repository import create_lead_activity, get_lead_by_id
from app.services.enquiry_intelligence_service import (
    enqueue_rebuild_activity_summary,
)


# Only manually-logged types may be edited. System-generated entries
# (status changes, follow-up events, invoice/payment audit rows) are
# immutable so the activity log keeps a trustworthy audit trail.
EDITABLE_ACTIVITY_TYPES: set[LeadActivityType] = {
    LeadActivityType.CALL,
    LeadActivityType.WHATSAPP,
    LeadActivityType.MEETING,
    LeadActivityType.NOTE,
}


def create_activity(
    session: Session,
    current_user: User,
    lead_id: UUID,
    data: LeadActivityCreate,
) -> LeadActivity:
    lead = get_lead_by_id(
        session=session,
        business_id=current_user.business_id,
        lead_id=lead_id,
    )
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )

    activity_data = data.model_dump()
    activity_data["lead_id"] = lead.id
    activity_data["created_by"] = current_user.id

    # Route through the chokepoint so actor_type / chat_session_id / task_id
    # get stamped from ActorContext AND the 0045 activity_summary trigger
    # fires (the chokepoint spawns the daemon thread post-commit). The
    # previous inline `session.add + commit` bypassed both.
    activity = LeadActivity(**activity_data)
    return create_lead_activity(session, activity)


def update_activity(
    session: Session,
    current_user: User,
    lead_id: UUID,
    activity_id: UUID,
    data: LeadActivityUpdate,
    background_tasks: Optional[BackgroundTasks] = None,
) -> LeadActivity:
    lead = get_lead_by_id(
        session=session,
        business_id=current_user.business_id,
        lead_id=lead_id,
    )
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )

    activity = session.exec(
        select(LeadActivity).where(
            LeadActivity.id == activity_id,
            LeadActivity.lead_id == lead.id,
        )
    ).first()
    if activity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found",
        )

    if activity.type not in EDITABLE_ACTIVITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System-generated activities cannot be edited",
        )

    payload = data.model_dump(exclude_unset=True)

    if "type" in payload:
        new_type = payload["type"]
        if new_type not in EDITABLE_ACTIVITY_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Activity type must be one of: call, whatsapp, meeting, note",
            )
        activity.type = new_type

    if "description" in payload:
        new_description = (payload["description"] or "").strip()
        if not new_description:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Description cannot be empty",
            )
        activity.description = new_description

    session.add(activity)
    session.commit()
    session.refresh(activity)

    # 0045 — edits invalidate the incremental activity_summary watermark
    # (the prior summary baked the old description in). Only a full rebuild
    # can rewrite history. Deletes hit the same trigger from their route.
    if background_tasks is not None:
        background_tasks.add_task(
            enqueue_rebuild_activity_summary, activity.lead_id,
        )

    return activity


def list_activities(
    session: Session,
    current_user: User,
    lead_id: UUID,
) -> List[LeadActivity]:
    lead = get_lead_by_id(
        session=session,
        business_id=current_user.business_id,
        lead_id=lead_id,
    )
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )

    statement = select(LeadActivity).where(LeadActivity.lead_id == lead.id)
    return list(session.exec(statement).all())


# Hard cap so a malicious / careless ?limit=999999 can't pin the DB.
_TIMELINE_MAX_LIMIT = 100


def list_activities_timeline(
    session: Session,
    current_user: User,
    lead_id: UUID,
    *,
    before: datetime | None = None,
    limit: int = 50,
) -> tuple[list[LeadActivity], datetime | None]:
    """Tenant-checked, cursor-paged diary read for one lead.

    Returns (activities, next_cursor). next_cursor = created_at of the
    last row (i.e., the oldest in this page), or None if the page didn't
    fill `limit` (= no more rows). The route surfaces this verbatim.
    """
    lead = get_lead_by_id(
        session=session,
        business_id=current_user.business_id,
        lead_id=lead_id,
    )
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )

    effective_limit = max(1, min(limit, _TIMELINE_MAX_LIMIT))
    rows = activity_repo.list_for_lead(
        session,
        lead_id=lead.id,
        before=before,
        limit=effective_limit,
    )
    next_cursor = rows[-1].created_at if len(rows) == effective_limit else None
    return rows, next_cursor

