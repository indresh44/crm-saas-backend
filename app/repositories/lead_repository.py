from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlmodel import Session, select

from app.models.customer import Customer
from app.models.lead import Lead, LeadActivity, LeadRead
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


def list_leads_for_business(
    session: Session,
    business_id: UUID,
    customer_id: UUID | None = None,
) -> list[LeadRead]:
    statement = (
        select(
            Lead,
            Customer.name,
            Customer.phone,
            PipelineStage.name,
            PipelineStage.color,
        )
        .outerjoin(Customer, Lead.customer_id == Customer.id)
        .outerjoin(PipelineStage, Lead.stage_id == PipelineStage.id)
        .where(Lead.business_id == business_id)
        .order_by(Lead.created_at.desc())
    )

    if customer_id is not None:
        statement = statement.where(Lead.customer_id == customer_id)

    rows = session.exec(statement).all()
    results: list[LeadRead] = []
    for lead, customer_name, customer_phone, stage_name, stage_color in rows:
        lead_read = LeadRead.model_validate(lead, from_attributes=True)
        lead_read.customer_name = customer_name
        lead_read.customer_phone = customer_phone
        lead_read.stage_name = stage_name
        lead_read.stage_color = stage_color
        results.append(lead_read)

    return results


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
    """The ONE chokepoint that mints LeadActivity rows.

    Stamps the diary fields (actor_type, chat_session_id, task_id) from the
    ambient `ActorContext` so callers don't have to thread them through every
    service signature. If a caller has already set these fields on the
    activity object (rare — only for tests or migrations that set explicit
    values), the caller's values win.

    Note for new emit sites added in batches after 0042: always set `payload`
    on the activity object yourself before calling this helper — the
    chokepoint does NOT synthesise payload (it can't; only the caller knows
    the event's facts). The source-grep test in
    `tests/test_lead_activity_emits_pg.py` enforces that every emit site for
    a SYSTEM-typed event sets payload."""
    # Local import to avoid a circular import at module load time
    # (actor_context imports nothing from app.models, but lead_repository is
    # imported very early in service modules; deferring keeps the import
    # graph clean).
    from app.core.actor_context import current_actor, warn_if_default_on_write

    warn_if_default_on_write()
    ctx = current_actor()

    if activity.actor_type is None:
        activity.actor_type = ctx.actor_type
    if activity.chat_session_id is None:
        activity.chat_session_id = ctx.chat_session_id
    if activity.task_id is None:
        activity.task_id = ctx.task_id

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
