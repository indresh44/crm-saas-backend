from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.lead import LeadActivityCreate, LeadActivityRead
from app.models.user import User
from app.services.lead_activity_service import (
    create_activity as service_create_activity,
    list_activities as service_list_activities,
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


@router.get("/leads/{lead_id}/activities", response_model=List[LeadActivityRead])
def list_lead_activities(
    lead_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[LeadActivityRead]:
    activities = service_list_activities(
        session=session,
        current_user=current_user,
        lead_id=lead_id,
    )
    return activities

