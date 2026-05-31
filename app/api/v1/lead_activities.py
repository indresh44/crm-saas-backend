from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.lead import LeadActivityCreate, LeadActivityRead, LeadActivityUpdate
from app.models.user import User
from app.services.lead_activity_service import (
    create_activity as service_create_activity,
    list_activities_timeline as service_list_activities_timeline,
    update_activity as service_update_activity,
)

router = APIRouter()


@router.post("/leads/{lead_id}/activities", response_model=LeadActivityRead)
def create_lead_activity(
    lead_id: UUID,
    payload: LeadActivityCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> LeadActivityRead:
    activity = service_create_activity(
        session=session,
        current_user=current_user,
        lead_id=lead_id,
        data=payload,
    )
    return activity


@router.patch(
    "/leads/{lead_id}/activities/{activity_id}",
    response_model=LeadActivityRead,
)
def update_lead_activity(
    lead_id: UUID,
    activity_id: UUID,
    payload: LeadActivityUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> LeadActivityRead:
    activity = service_update_activity(
        session=session,
        current_user=current_user,
        lead_id=lead_id,
        activity_id=activity_id,
        data=payload,
    )
    return activity


class LeadActivityTimelineResponse(BaseModel):
    activities: List[LeadActivityRead]
    next_cursor: Optional[datetime] = None


@router.get(
    "/leads/{lead_id}/activities",
    response_model=LeadActivityTimelineResponse,
)
def list_lead_activities(
    lead_id: UUID,
    before: Optional[datetime] = Query(
        default=None,
        description="Cursor: created_at of last row from previous page.",
    ),
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> LeadActivityTimelineResponse:
    activities, next_cursor = service_list_activities_timeline(
        session=session,
        current_user=current_user,
        lead_id=lead_id,
        before=before,
        limit=limit,
    )
    return LeadActivityTimelineResponse(
        activities=activities,
        next_cursor=next_cursor,
    )

