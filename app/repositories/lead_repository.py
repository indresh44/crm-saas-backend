from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlmodel import Session, select

from app.models.lead import Lead, LeadActivity
from app.models.pipeline import Pipeline, PipelineStage


def create_lead(session: Session, lead: Lead) -> Lead:
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead


def update_lead(session: Session, lead: Lead) -> Lead:
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead


def get_lead_by_id(session: Session, business_id: UUID, lead_id: UUID) -> Lead | None:
    statement = select(Lead).where(Lead.id == lead_id, Lead.business_id == business_id)
    return session.exec(statement).first()


def list_leads_for_business(session: Session, business_id: UUID) -> list[Lead]:
    statement = select(Lead).where(Lead.business_id == business_id)
    return list(session.exec(statement).all())


def move_lead_stage(session: Session, lead: Lead, new_stage_id: UUID) -> Lead:
    lead.stage_id = new_stage_id
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead


def get_pipeline_stage_by_id(
    session: Session,
    business_id: UUID,
    stage_id: UUID,
) -> PipelineStage | None:
    statement = (
        select(PipelineStage)
        .join(Pipeline, PipelineStage.pipeline_id == Pipeline.id)
        .where(PipelineStage.id == stage_id, Pipeline.business_id == business_id)
    )
    return session.exec(statement).first()


def create_lead_activity(session: Session, activity: LeadActivity) -> LeadActivity:
    session.add(activity)
    session.commit()
    session.refresh(activity)
    return activity


def get_pipeline_by_id_for_business(
    session: Session,
    business_id: UUID,
    pipeline_id: UUID,
) -> Pipeline | None:
    statement = select(Pipeline).where(Pipeline.id == pipeline_id, Pipeline.business_id == business_id)
    return session.exec(statement).first()


def list_stages_for_pipeline(session: Session, pipeline_id: UUID) -> list[PipelineStage]:
    statement = (
        select(PipelineStage)
        .where(PipelineStage.pipeline_id == pipeline_id)
        .order_by(PipelineStage.position)
    )
    return list(session.exec(statement).all())


def list_leads_for_business_and_stage_ids(
    session: Session,
    business_id: UUID,
    stage_ids: Sequence[UUID],
) -> list[Lead]:
    if not stage_ids:
        return []

    statement = select(Lead).where(
        Lead.business_id == business_id,
        Lead.stage_id.in_(list(stage_ids)),
    )
    return list(session.exec(statement).all())

