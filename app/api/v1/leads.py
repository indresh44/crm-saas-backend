from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.lead import LeadCreate, LeadMoveRequest, LeadRead, LeadUpdate
from app.models.user import User
from app.services.lead_service import (
    create_lead as service_create_lead,
    get_lead as service_get_lead,
    get_todays_followups as service_get_todays_followups,
    list_leads as service_list_leads,
    move_lead_stage as service_move_lead_stage,
    update_lead as service_update_lead,
)

router = APIRouter()


@router.post("/leads", response_model=LeadRead)
def create_lead(
    payload: LeadCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> LeadRead:
    lead = service_create_lead(session=session, current_user=current_user, data=payload)
    return lead


@router.get("/leads", response_model=List[LeadRead])
def list_leads(
    follow_up_today: bool = False,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[LeadRead]:
    if follow_up_today:
        leads = service_get_todays_followups(
            session=session,
            business_id=current_user.business_id,
        )
    else:
        leads = service_list_leads(session=session, current_user=current_user)
    return leads


@router.get("/leads/{lead_id}", response_model=LeadRead)
def get_lead(
    lead_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> LeadRead:
    lead = service_get_lead(session=session, current_user=current_user, lead_id=lead_id)
    return lead


@router.patch("/leads/{lead_id}", response_model=LeadRead)
def update_lead(
    lead_id: UUID,
    payload: LeadUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> LeadRead:
    lead = service_update_lead(
        session=session,
        current_user=current_user,
        lead_id=lead_id,
        data=payload,
    )
    return lead


@router.post("/leads/{lead_id}/move", response_model=LeadRead)
def move_lead(
    lead_id: UUID,
    payload: LeadMoveRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> LeadRead:
    lead = service_move_lead_stage(
        session=session,
        current_user=current_user,
        lead_id=lead_id,
        new_stage_id=payload.stage_id,
    )
    return lead
