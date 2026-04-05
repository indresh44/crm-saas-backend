from datetime import date, datetime, time, timedelta, timezone
from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.lead import Lead
from app.models.lead_followup import (
    LeadFollowup,
    LeadFollowupCreate,
    LeadFollowupDone,
    LeadFollowupTodayRead,
    LeadFollowupUpdate,
)
from app.models.user import User
from app.repositories.lead_followup_repository import (
    create_lead_followup as repo_create_lead_followup,
    get_lead_followup_by_id,
    list_followups_before_datetime,
    list_followups_for_datetime_range,
    list_followups_for_lead,
    list_followups_with_filters,
    update_lead_followup as repo_update_lead_followup,
)
from app.repositories.lead_repository import get_lead_by_id


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
    return repo_create_lead_followup(session, followup)


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
    start_at = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    end_at = start_at + timedelta(days=1)

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

    return repo_update_lead_followup(session, followup)


def update_followup(
    session: Session,
    current_user: User,
    followup_id: UUID,
    data: LeadFollowupUpdate,
) -> LeadFollowup:
    followup = get_followup(session, current_user, followup_id)

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

    return repo_update_lead_followup(session, followup)
