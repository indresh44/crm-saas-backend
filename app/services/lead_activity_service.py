from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.lead import LeadActivity, LeadActivityCreate
from app.models.user import User
from app.repositories.lead_repository import get_lead_by_id


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

    activity = LeadActivity(**activity_data)
    session.add(activity)
    session.commit()
    session.refresh(activity)
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

