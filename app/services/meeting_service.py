from datetime import date
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.enums import LeadActivityType, MeetingStatus
from app.models.lead import LeadActivity
from app.models.meeting import Meeting, MeetingCreate, MeetingUpdate
from app.models.user import User
from app.repositories.customer_repository import get_customer_by_id
from app.repositories.lead_repository import create_lead_activity, get_lead_by_id
from app.repositories.meeting_repository import (
    create_meeting as repo_create_meeting,
    delete_meeting as repo_delete_meeting,
    get_meeting_by_id,
    list_meetings as repo_list_meetings,
    update_meeting as repo_update_meeting,
)


def _validate_customer(session: Session, business_id: UUID, customer_id: UUID) -> None:
    customer = get_customer_by_id(
        session=session,
        business_id=business_id,
        customer_id=customer_id,
    )
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Customer not found for this business",
        )


def _validate_lead_for_customer(
    session: Session,
    business_id: UUID,
    lead_id: UUID,
    customer_id: UUID,
):
    lead = get_lead_by_id(
        session=session,
        business_id=business_id,
        lead_id=lead_id,
    )
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lead not found for this business",
        )

    if lead.customer_id != customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lead does not belong to this customer",
        )

    return lead


def create_meeting(session: Session, current_user: User, data: MeetingCreate) -> Meeting:
    _validate_customer(session, current_user.business_id, data.customer_id)

    if data.lead_id is not None:
        _validate_lead_for_customer(
            session=session,
            business_id=current_user.business_id,
            lead_id=data.lead_id,
            customer_id=data.customer_id,
        )

    meeting_data = data.model_dump()
    meeting_data["business_id"] = current_user.business_id
    meeting = Meeting(**meeting_data)
    return repo_create_meeting(session, meeting)


def list_meetings(
    session: Session,
    current_user: User,
    customer_id: UUID | None = None,
    lead_id: UUID | None = None,
    status: MeetingStatus | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[Meeting]:
    return repo_list_meetings(
        session=session,
        business_id=current_user.business_id,
        customer_id=customer_id,
        lead_id=lead_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
    )


def get_meeting(session: Session, current_user: User, meeting_id: UUID) -> Meeting:
    meeting = get_meeting_by_id(session, business_id=current_user.business_id, meeting_id=meeting_id)
    if meeting is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found",
        )
    return meeting


def update_meeting(
    session: Session,
    current_user: User,
    meeting_id: UUID,
    data: MeetingUpdate,
) -> Meeting:
    meeting = get_meeting(session, current_user, meeting_id)

    update_data = data.model_dump(exclude_unset=True)
    target_customer_id = update_data.get("customer_id", meeting.customer_id)
    target_lead_id = update_data.get("lead_id", meeting.lead_id)

    if "customer_id" in update_data:
        _validate_customer(session, current_user.business_id, target_customer_id)

    if target_lead_id is not None:
        _validate_lead_for_customer(
            session=session,
            business_id=current_user.business_id,
            lead_id=target_lead_id,
            customer_id=target_customer_id,
        )

    status_changed_to_completed = (
        "status" in update_data
        and update_data["status"] == MeetingStatus.COMPLETED
        and meeting.status != MeetingStatus.COMPLETED
    )

    for field, value in update_data.items():
        setattr(meeting, field, value)

    updated_meeting = repo_update_meeting(session, meeting)

    if status_changed_to_completed and updated_meeting.lead_id is not None:
        activity = LeadActivity(
            lead_id=updated_meeting.lead_id,
            type=LeadActivityType.MEETING,
            description=f"Meeting completed: {updated_meeting.title}",
            created_by=current_user.id,
        )
        create_lead_activity(session, activity)

    return updated_meeting


def delete_meeting(session: Session, current_user: User, meeting_id: UUID) -> None:
    meeting = get_meeting(session, current_user, meeting_id)

    if meeting.status != MeetingStatus.SCHEDULED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete a completed or cancelled meeting",
        )

    repo_delete_meeting(session, meeting)
