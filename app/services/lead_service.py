from datetime import datetime, time, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.enums import LeadActivityType
from app.models.lead import Lead, LeadActivity, LeadCreate, LeadRead, LeadUpdate
from app.models.user import User
from app.repositories.lead_repository import (
    create_lead as repo_create_lead,
    create_lead_activity,
    get_lead_by_id,
    get_pipeline_stage_by_id,
    list_leads_for_business,
    move_lead_stage as repo_move_lead_stage,
    update_lead as repo_update_lead,
)


def create_lead(session: Session, current_user: User, data: LeadCreate) -> Lead:
    lead_data = data.model_dump()
    lead_data["business_id"] = current_user.business_id

    stage = get_pipeline_stage_by_id(
        session=session,
        business_id=current_user.business_id,
        stage_id=lead_data["stage_id"],
    )
    if stage is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stage not found for current business",
        )

    lead = Lead(**lead_data)
    return repo_create_lead(session, lead)


def get_lead(session: Session, current_user: User, lead_id: UUID) -> Lead:
    lead = get_lead_by_id(session, business_id=current_user.business_id, lead_id=lead_id)
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )
    return lead


def list_leads(
    session: Session,
    current_user: User,
    customer_id: UUID | None = None,
) -> list[LeadRead]:
    return list_leads_for_business(
        session,
        business_id=current_user.business_id,
        customer_id=customer_id,
    )


def search_leads(
    session: Session,
    current_user: User,
    query: str,
    limit: int = 10,
) -> list[LeadRead]:
    cleaned_query = query.strip().lower()
    if len(cleaned_query) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query must be at least 2 characters long",
        )

    leads = list_leads(session=session, current_user=current_user)
    matches = [
        lead
        for lead in leads
        if cleaned_query in (lead.title or "").lower()
        or cleaned_query in (lead.customer_name or "").lower()
        or cleaned_query in (lead.notes or "").lower()
    ]
    return matches[:limit]


def get_todays_followups(
    session: Session,
    business_id: UUID,
    customer_id: UUID | None = None,
) -> list[Lead]:
    today_start = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    today_end = today_start + timedelta(days=1)
    statement = select(Lead).where(
        Lead.business_id == business_id,
        Lead.follow_up_at.is_not(None),
        Lead.follow_up_at >= today_start,
        Lead.follow_up_at < today_end,
    )
    if customer_id is not None:
        statement = statement.where(Lead.customer_id == customer_id)
    return list(session.exec(statement).all())


def update_lead(
    session: Session,
    current_user: User,
    lead_id: UUID,
    data: LeadUpdate,
) -> Lead:
    lead = get_lead(session, current_user, lead_id)

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(lead, field, value)

    return repo_update_lead(session, lead)


def move_lead_stage(
    session: Session,
    current_user: User,
    lead_id: UUID,
    new_stage_id: UUID,
) -> Lead:
    lead = get_lead(session, current_user, lead_id)

    if lead.stage_id == new_stage_id:
        return lead

    new_stage = get_pipeline_stage_by_id(
        session=session,
        business_id=current_user.business_id,
        stage_id=new_stage_id,
    )
    if new_stage is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stage not found for current business",
        )

    old_stage = get_pipeline_stage_by_id(
        session=session,
        business_id=current_user.business_id,
        stage_id=lead.stage_id,
    )

    updated_lead = repo_move_lead_stage(session, lead, new_stage_id)

    old_label = getattr(old_stage, "name", str(lead.stage_id)) if old_stage is not None else str(lead.stage_id)
    new_label = getattr(new_stage, "name", str(new_stage.id))

    activity = LeadActivity(
        lead_id=updated_lead.id,
        type=LeadActivityType.STATUS_CHANGE,
        description=f"Stage moved from {old_label} to {new_label}",
        created_by=current_user.id,
    )
    create_lead_activity(session, activity)

    return updated_lead
