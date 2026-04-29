from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.enums import LeadActivityType
from app.models.lead import LeadActivity, LeadActivityCreate, LeadActivityUpdate
from app.models.user import User
from app.repositories.lead_repository import get_lead_by_id


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

    activity = LeadActivity(**activity_data)
    session.add(activity)
    session.commit()
    session.refresh(activity)
    return activity


def update_activity(
    session: Session,
    current_user: User,
    lead_id: UUID,
    activity_id: UUID,
    data: LeadActivityUpdate,
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

