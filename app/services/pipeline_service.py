from typing import List
import uuid
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.pipeline import Pipeline, PipelineCreate, PipelineStage
from app.models.user import User
from app.repositories.pipeline_repository import (
    create_pipeline as repo_create_pipeline,
    create_pipeline_stage as repo_create_pipeline_stage,
    get_pipeline_by_business,
    get_pipeline_by_id_for_business,
    get_stages_by_business,
    get_stage_by_id_for_business,
    list_pipelines_for_business,
    list_stages_for_pipeline,
    update_pipeline_stage as repo_update_pipeline_stage,
)


def create_pipeline(
    session: Session,
    current_user: User,
    data: PipelineCreate,
) -> Pipeline:
    pipeline_data = data.model_dump()
    pipeline_data["business_id"] = current_user.business_id
    pipeline = Pipeline(**pipeline_data)
    return repo_create_pipeline(session, pipeline)


def list_pipelines(session: Session, current_user: User) -> List[Pipeline]:
    return list_pipelines_for_business(session, business_id=current_user.business_id)


def get_pipeline(
    session: Session,
    current_user: User,
    pipeline_id: UUID,
) -> Pipeline:
    pipeline = get_pipeline_by_id_for_business(
        session=session,
        business_id=current_user.business_id,
        pipeline_id=pipeline_id,
    )
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline not found",
        )
    return pipeline


def list_stages_for_pipeline_for_user(
    session: Session,
    current_user: User,
    pipeline_id: UUID,
) -> List[PipelineStage]:
    _ = get_pipeline(session, current_user, pipeline_id)
    return list_stages_for_pipeline(session=session, pipeline_id=pipeline_id)


def get_stages_for_business(
    session: Session,
    current_user: User,
) -> List[PipelineStage]:
    return get_stages_by_business(session, current_user.business_id)


PIPELINE_TEMPLATES: dict[str, list[dict]] = {
    "interior_designer": [
        {"name": "New Enquiry", "position": 1, "color": "#6366f1"},
        {"name": "Interested", "position": 2, "color": "#f59e0b"},
        {"name": "Site Visit Scheduled", "position": 3, "color": "#8b5cf6"},
        {"name": "WIP", "position": 4, "color": "#3b82f6"},
        {"name": "Completed", "position": 5, "color": "#10b981"},
        {"name": "Lost", "position": 6, "color": "#ef4444"},
    ],
    "photographer": [
        {"name": "New Enquiry", "position": 1, "color": "#6366f1"},
        {"name": "Interested", "position": 2, "color": "#f59e0b"},
        {"name": "Shoot Scheduled", "position": 3, "color": "#8b5cf6"},
        {"name": "Delivered", "position": 4, "color": "#3b82f6"},
        {"name": "Completed", "position": 5, "color": "#10b981"},
        {"name": "Lost", "position": 6, "color": "#ef4444"},
    ],
    "coach": [
        {"name": "New Enquiry", "position": 1, "color": "#6366f1"},
        {"name": "Interested", "position": 2, "color": "#f59e0b"},
        {"name": "Trial Session", "position": 3, "color": "#8b5cf6"},
        {"name": "Completed", "position": 4, "color": "#10b981"},
        {"name": "Lost", "position": 5, "color": "#ef4444"},
    ],
    "other": [
        {"name": "New Enquiry", "position": 1, "color": "#6366f1"},
        {"name": "Interested", "position": 2, "color": "#f59e0b"},
        {"name": "Completed", "position": 3, "color": "#10b981"},
        {"name": "Lost", "position": 4, "color": "#ef4444"},
    ],
}


def create_default_pipeline(session: Session, business_id: uuid.UUID) -> Pipeline:
    """
    Creates one pipeline with generic stages for a new business.
    Safe to call only if no pipeline exists yet for this business.
    """
    return create_persona_pipeline(session, business_id, "other")


def create_persona_pipeline(
    session: Session,
    business_id: uuid.UUID,
    persona: str,
) -> Pipeline:
    """
    Creates a pipeline with persona-specific stages.
    If a pipeline already exists, returns it unchanged.
    """
    existing = get_pipeline_by_business(session, business_id)
    if existing:
        return existing

    stages = PIPELINE_TEMPLATES.get(persona, PIPELINE_TEMPLATES["other"])

    pipeline = Pipeline(
        name="Sales Pipeline",
        business_id=business_id,
        is_default=True,
    )
    session.add(pipeline)
    session.flush()

    for s in stages:
        session.add(
            PipelineStage(
                pipeline_id=pipeline.id,
                **s,
            )
        )
    session.commit()
    return pipeline


def ensure_pipeline_exists(session: Session, business_id: uuid.UUID) -> Pipeline:
    """
    Safety net: ensures a pipeline exists for a business.
    Called when a lead is being created but onboarding may have been skipped.
    """
    existing = get_pipeline_by_business(session, business_id)
    if existing:
        return existing
    return create_default_pipeline(session, business_id)


def create_stage(
    session: Session,
    current_user: User,
    data: "PipelineStageCreateForUser",
) -> PipelineStage:
    pipeline = get_pipeline(
        session=session,
        current_user=current_user,
        pipeline_id=data.pipeline_id,
    )

    existing_stages = list_stages_for_pipeline(session=session, pipeline_id=pipeline.id)
    next_position = max((s.position for s in existing_stages), default=0) + 1

    stage = PipelineStage(
        pipeline_id=pipeline.id,
        name=data.name,
        position=data.position or next_position,
        color=data.color,
    )
    return repo_create_pipeline_stage(session, stage)


def update_stage(
    session: Session,
    current_user: User,
    stage_id: UUID,
    data: "PipelineStageUpdate",
) -> PipelineStage:
    stage = get_stage_by_id_for_business(
        session=session,
        business_id=current_user.business_id,
        stage_id=stage_id,
    )
    if stage is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stage not found",
        )

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(stage, field, value)

    return repo_update_pipeline_stage(session, stage)


from typing import Optional  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402


class PipelineStageCreateForUser(SQLModel):
    pipeline_id: UUID
    name: str
    color: str
    position: Optional[int] = None


class PipelineStageUpdate(SQLModel):
    name: Optional[str] = None
    color: Optional[str] = None
    position: Optional[int] = None

