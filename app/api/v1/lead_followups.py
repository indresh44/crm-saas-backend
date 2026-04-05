from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.lead_followup import LeadFollowupCreate, LeadFollowupDone, LeadFollowupRead, LeadFollowupTodayRead
from app.models.user import User
from app.services.lead_followup_service import (
    create_followup as service_create_followup,
    list_followups as service_list_followups,
    list_todays_followups as service_list_todays_followups,
    mark_followup_done as service_mark_followup_done,
)

router = APIRouter()


@router.post("/lead_followups", response_model=LeadFollowupRead)
def create_lead_followup(
    payload: LeadFollowupCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> LeadFollowupRead:
    followup = service_create_followup(
        session=session,
        current_user=current_user,
        data=payload,
    )
    return followup


@router.get("/lead_followups", response_model=List[LeadFollowupRead])
def list_lead_followups(
    lead_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[LeadFollowupRead]:
    followups = service_list_followups(
        session=session,
        current_user=current_user,
        lead_id=lead_id,
    )
    return followups


@router.get("/lead_followups/today", response_model=List[LeadFollowupTodayRead])
def list_todays_followups(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[LeadFollowupTodayRead]:
    followups = service_list_todays_followups(
        session=session,
        current_user=current_user,
    )
    return followups


@router.patch("/lead_followups/{followup_id}/done", response_model=LeadFollowupRead)
def mark_lead_followup_done(
    followup_id: UUID,
    payload: Optional[LeadFollowupDone] = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> LeadFollowupRead:
    followup = service_mark_followup_done(
        session=session,
        current_user=current_user,
        followup_id=followup_id,
        data=payload or LeadFollowupDone(),
    )
    return followup
