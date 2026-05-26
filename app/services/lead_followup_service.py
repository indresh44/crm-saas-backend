from datetime import date, datetime, time, timedelta, timezone
from typing import Any, List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.core.json_safe import safe_jsonify
from app.core.time_utils import day_bounds_utc, format_local, today_in
from app.models.enums import LeadActivityType
from app.models.lead import Lead, LeadActivity
from app.models.lead_followup import (
    LeadFollowup,
    LeadFollowupCancel,
    LeadFollowupCreate,
    LeadFollowupDone,
    LeadFollowupReschedule,
    LeadFollowupTodayRead,
    LeadFollowupUpdate,
)
from app.models.user import User
from app.repositories.business_repository import get_business_by_id
from app.repositories.lead_followup_repository import (
    create_lead_followup as repo_create_lead_followup,
    get_lead_followup_by_id,
    list_followups_before_datetime,
    list_followups_for_datetime_range,
    list_followups_for_lead,
    list_followups_with_filters,
    update_lead_followup as repo_update_lead_followup,
)
from app.repositories.lead_repository import create_lead_activity, get_lead_by_id


def _business_timezone(session: Session, business_id: UUID) -> str:
    """Fetch the business's configured IANA timezone. Falls back to the helper default if absent."""
    business = get_business_by_id(session, business_id)
    if business is None:
        return "Asia/Kolkata"
    return business.timezone or "Asia/Kolkata"


def _get_lead_for_business(
    session: Session,
    business_id: UUID,
    lead_id: UUID,
) -> Lead:
    lead = get_lead_by_id(
        session=session,
        business_id=business_id,
        lead_id=lead_id,
    )
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )
    return lead


def _attach_lead_titles(
    session: Session,
    business_id: UUID,
    followups: list[LeadFollowup],
) -> list[LeadFollowupTodayRead]:
    lead_ids = {followup.lead_id for followup in followups}
    if not lead_ids:
        return []

    statement = select(Lead.id, Lead.title).where(
        Lead.business_id == business_id,
        Lead.id.in_(lead_ids),
    )
    lead_title_by_id = {
        lead_id: lead_title
        for lead_id, lead_title in session.exec(statement).all()
    }

    business_followups: list[LeadFollowupTodayRead] = []
    for followup in followups:
        lead_title = lead_title_by_id.get(followup.lead_id)
        if lead_title is None:
            continue
        business_followups.append(
            LeadFollowupTodayRead(
                id=followup.id,
                lead_id=followup.lead_id,
                scheduled_at=followup.scheduled_at,
                note=followup.note,
                status=followup.status,
                created_by=followup.created_by,
                created_at=followup.created_at,
                completed_at=followup.completed_at,
                lead_title=lead_title,
            )
        )
    return business_followups


def create_followup(
    session: Session,
    current_user: User,
    data: LeadFollowupCreate,
) -> LeadFollowup:
    lead = _get_lead_for_business(session, current_user.business_id, data.lead_id)
    followup = LeadFollowup(
        lead_id=lead.id,
        scheduled_at=data.scheduled_at,
        note=data.note,
        created_by=current_user.id,
    )
    result = repo_create_lead_followup(session, followup)
    tz = _business_timezone(session, current_user.business_id)
    create_lead_activity(session, LeadActivity(
        lead_id=lead.id,
        type=LeadActivityType.FOLLOWUP_SCHEDULED,
        description=f"Follow-up scheduled for {format_local(result.scheduled_at, tz)}"
                   + (f" — {result.note}" if result.note else ""),
        created_by=current_user.id,
        payload=safe_jsonify({
            "followup_id": result.id,
            "scheduled_at": result.scheduled_at,
            "note": result.note,
        }),
    ))
    return result


def list_followups(
    session: Session,
    current_user: User,
    lead_id: UUID | None = None,
    *,
    customer_id: UUID | None = None,
    status: str | None = None,
    date_value: date | None = None,
    before_date: date | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    followup_ids: list[UUID] | None = None,
    older_than_days: int | None = None,
    limit: int = 50,
) -> List[LeadFollowup]:
    if lead_id is not None and not any(
        value is not None
        for value in [customer_id, status, date_value, before_date, from_date, to_date, followup_ids, older_than_days]
    ):
        lead = _get_lead_for_business(session, current_user.business_id, lead_id)
        return list_followups_for_lead(session, lead_id=lead.id)

    if lead_id is not None:
        _get_lead_for_business(session, current_user.business_id, lead_id)

    return list_followups_with_filters(
        session=session,
        business_id=current_user.business_id,
        lead_id=lead_id,
        customer_id=customer_id,
        followup_ids=followup_ids,
        status=status,
        date_value=date_value,
        before_date=before_date,
        from_date=from_date,
        to_date=to_date,
        older_than_days=older_than_days,
        limit=limit,
    )


def get_followup(
    session: Session,
    current_user: User,
    followup_id: UUID,
) -> LeadFollowup:
    followup = get_lead_followup_by_id(session, followup_id=followup_id)
    if followup is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Follow-up not found",
        )

    _get_lead_for_business(session, current_user.business_id, followup.lead_id)
    return followup


def list_todays_followups(
    session: Session,
    current_user: User,
) -> List[LeadFollowupTodayRead]:
    tz = _business_timezone(session, current_user.business_id)
    start_at, end_at = day_bounds_utc(today_in(tz), tz)

    followups = list_followups_for_datetime_range(
        session=session,
        start_at=start_at,
        end_at=end_at,
    )
    return _attach_lead_titles(session, current_user.business_id, followups)


def list_overdue_followups(
    session: Session,
    current_user: User,
) -> List[LeadFollowup]:
    now = datetime.now(timezone.utc)
    followups = list_followups_before_datetime(
        session=session,
        before_at=now,
    )
    business_lead_ids = {
        followup.lead_id
        for followup in followups
        if get_lead_by_id(session, current_user.business_id, followup.lead_id) is not None
    }
    return [followup for followup in followups if followup.lead_id in business_lead_ids]


def mark_followup_done(
    session: Session,
    current_user: User,
    followup_id: UUID,
    data: LeadFollowupDone,
) -> LeadFollowup:
    followup = get_followup(session, current_user, followup_id)
    followup.status = "done"
    followup.completed_at = datetime.now(timezone.utc)
    if data.note is not None:
        followup.note = data.note

    result = repo_update_lead_followup(session, followup)
    create_lead_activity(session, LeadActivity(
        lead_id=followup.lead_id,
        type=LeadActivityType.FOLLOWUP_COMPLETED,
        description="Follow-up marked as done" + (f" — {result.note}" if result.note else ""),
        created_by=current_user.id,
        payload=safe_jsonify({
            "followup_id": result.id,
            "note": result.note,
        }),
    ))
    return result


def update_followup(
    session: Session,
    current_user: User,
    followup_id: UUID,
    data: LeadFollowupUpdate,
) -> LeadFollowup:
    followup = get_followup(session, current_user, followup_id)
    old_status = followup.status
    old_scheduled_at = followup.scheduled_at

    if data.scheduled_at is not None:
        followup.scheduled_at = data.scheduled_at
    if data.note is not None:
        followup.note = data.note
    if data.status is not None:
        followup.status = data.status
        if data.status == "done" and data.completed_at is None and followup.completed_at is None:
            followup.completed_at = datetime.now(timezone.utc)
        elif data.status != "done" and data.completed_at is None:
            followup.completed_at = None
    if data.completed_at is not None:
        followup.completed_at = data.completed_at

    result = repo_update_lead_followup(session, followup)

    activity_type = None
    desc = ""
    activity_payload: dict[str, Any] | None = None
    if data.status == "done" and old_status != "done":
        activity_type = LeadActivityType.FOLLOWUP_COMPLETED
        desc = "Follow-up marked as done" + (f" — {result.note}" if result.note else "")
        activity_payload = {"followup_id": result.id, "note": result.note}
    elif data.status == "cancelled" and old_status != "cancelled":
        activity_type = LeadActivityType.FOLLOWUP_CANCELLED
        desc = "Follow-up cancelled" + (f" — {result.note}" if result.note else "")
        activity_payload = {"followup_id": result.id, "note": result.note}
    elif data.scheduled_at is not None and data.scheduled_at != old_scheduled_at:
        activity_type = LeadActivityType.FOLLOWUP_RESCHEDULED
        tz = _business_timezone(session, current_user.business_id)
        desc = f"Follow-up rescheduled to {format_local(result.scheduled_at, tz)}" + (f" — {result.note}" if result.note else "")
        activity_payload = {
            "followup_id": result.id,
            "old_scheduled_at": old_scheduled_at,
            "new_scheduled_at": result.scheduled_at,
        }

    if activity_type:
        create_lead_activity(session, LeadActivity(
            lead_id=followup.lead_id,
            type=activity_type,
            description=desc,
            created_by=current_user.id,
            payload=safe_jsonify(activity_payload) if activity_payload else None,
        ))

    return result


def reschedule_followup(
    session: Session,
    current_user: User,
    followup_id: UUID,
    data: LeadFollowupReschedule,
) -> LeadFollowup:
    followup = get_followup(session, current_user, followup_id)
    if followup.status in {"done", "cancelled"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot reschedule a {followup.status} follow-up.",
        )
    return update_followup(
        session=session,
        current_user=current_user,
        followup_id=followup_id,
        data=LeadFollowupUpdate(
            scheduled_at=data.scheduled_at,
            note=data.note,
        ),
    )


def cancel_followup(
    session: Session,
    current_user: User,
    followup_id: UUID,
    data: LeadFollowupCancel,
) -> LeadFollowup:
    followup = get_followup(session, current_user, followup_id)
    if followup.status in {"done", "cancelled"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Follow-up is already {followup.status}.",
        )
    return update_followup(
        session=session,
        current_user=current_user,
        followup_id=followup_id,
        data=LeadFollowupUpdate(
            status="cancelled",
            note=data.note,
        ),
    )
