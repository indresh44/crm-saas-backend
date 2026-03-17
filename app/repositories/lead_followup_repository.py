from datetime import datetime
from typing import List, Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.lead_followup import LeadFollowup


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


def update_lead_followup(session: Session, followup: LeadFollowup) -> LeadFollowup:
    session.add(followup)
    session.commit()
    session.refresh(followup)
    return followup
