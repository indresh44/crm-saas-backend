from datetime import date
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.enums import MeetingStatus
from app.models.meeting import MeetingCreate, MeetingRead, MeetingUpdate
from app.models.user import User
from app.services.meeting_service import (
    create_meeting as service_create_meeting,
    delete_meeting as service_delete_meeting,
    get_meeting as service_get_meeting,
    list_meetings as service_list_meetings,
    update_meeting as service_update_meeting,
)

router = APIRouter()


@router.post("/meetings", response_model=MeetingRead, status_code=status.HTTP_201_CREATED)
def create_meeting(
    payload: MeetingCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> MeetingRead:
    meeting = service_create_meeting(session=session, current_user=current_user, data=payload)
    return meeting


@router.get("/meetings", response_model=List[MeetingRead])
def list_meetings(
    customer_id: UUID | None = None,
    lead_id: UUID | None = None,
    status: MeetingStatus | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[MeetingRead]:
    meetings = service_list_meetings(
        session=session,
        current_user=current_user,
        customer_id=customer_id,
        lead_id=lead_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
    )
    return meetings


@router.get("/meetings/{meeting_id}", response_model=MeetingRead)
def get_meeting(
    meeting_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> MeetingRead:
    meeting = service_get_meeting(session=session, current_user=current_user, meeting_id=meeting_id)
    return meeting


@router.patch("/meetings/{meeting_id}", response_model=MeetingRead)
def update_meeting(
    meeting_id: UUID,
    payload: MeetingUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> MeetingRead:
    meeting = service_update_meeting(
        session=session,
        current_user=current_user,
        meeting_id=meeting_id,
        data=payload,
    )
    return meeting


@router.delete("/meetings/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meeting(
    meeting_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Response:
    service_delete_meeting(session=session, current_user=current_user, meeting_id=meeting_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
