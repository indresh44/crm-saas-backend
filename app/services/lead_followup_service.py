from datetime import datetime, time, timedelta, timezone
from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.lead import Lead
from app.models.lead_followup import LeadFollowup, LeadFollowupCreate, LeadFollowupDone
from app.models.user import User
from app.repositories.lead_followup_repository import (
    create_lead_followup as repo_create_lead_followup,
    get_lead_followup_by_id,
    list_followups_before_datetime,
    list_followups_for_datetime_range,
    list_followups_for_lead,
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
    lead_id: UUID,
) -> List[LeadFollowup]:
    lead = _get_lead_for_business(session, current_user.business_id, lead_id)
    return list_followups_for_lead(session, lead_id=lead.id)


def list_todays_followups(
    session: Session,
    current_user: User,
) -> List[LeadFollowup]:
    start_at = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    end_at = start_at + timedelta(days=1)

    followups = list_followups_for_datetime_range(
        session=session,
        start_at=start_at,
        end_at=end_at,
    )
    business_lead_ids = {
        followup.lead_id
        for followup in followups
        if get_lead_by_id(session, current_user.business_id, followup.lead_id) is not None
    }
    return [followup for followup in followups if followup.lead_id in business_lead_ids]


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
    followup = get_lead_followup_by_id(session, followup_id=followup_id)
    if followup is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Follow-up not found",
        )

    _get_lead_for_business(session, current_user.business_id, followup.lead_id)

    followup.status = "done"
    followup.completed_at = datetime.now(timezone.utc)
    if data.note is not None:
        followup.note = data.note

    return repo_update_lead_followup(session, followup)
