from typing import List, Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.pipeline import Pipeline, PipelineStage


def create_pipeline(session: Session, pipeline: Pipeline) -> Pipeline:
    session.add(pipeline)
    session.commit()
    session.refresh(pipeline)
    return pipeline


def get_pipeline_by_id_for_business(
    session: Session,
    business_id: UUID,
    pipeline_id: UUID,
) -> Optional[Pipeline]:
    statement = select(Pipeline).where(
        Pipeline.id == pipeline_id,
        Pipeline.business_id == business_id,
    )
    return session.exec(statement).first()


def list_pipelines_for_business(session: Session, business_id: UUID) -> List[Pipeline]:
    statement = select(Pipeline).where(Pipeline.business_id == business_id)
    return list(session.exec(statement).all())


def get_pipeline_by_business(session: Session, business_id: UUID) -> Optional[Pipeline]:
    return session.exec(select(Pipeline).where(Pipeline.business_id == business_id)).first()


def create_pipeline_stage(session: Session, stage: PipelineStage) -> PipelineStage:
    parent_pipeline = session.exec(
        select(Pipeline).where(Pipeline.id == stage.pipeline_id)
    ).first()
    if parent_pipeline is None:
        raise ValueError("Parent pipeline not found")

    session.add(stage)
    session.commit()
    session.refresh(stage)
    return stage


def get_stage_by_id_for_business(
    session: Session,
    business_id: UUID,
    stage_id: UUID,
) -> Optional[PipelineStage]:
    statement = (
        select(PipelineStage)
        .join(Pipeline, PipelineStage.pipeline_id == Pipeline.id)
        .where(PipelineStage.id == stage_id, Pipeline.business_id == business_id)
    )
    return session.exec(statement).first()


def list_stages_for_pipeline(session: Session, pipeline_id: UUID) -> List[PipelineStage]:
    statement = (
        select(PipelineStage)
        .where(PipelineStage.pipeline_id == pipeline_id)
        .order_by(PipelineStage.position)
    )
    return list(session.exec(statement).all())


def get_stages_by_business(session: Session, business_id: UUID) -> List[PipelineStage]:
    return list(
        session.exec(
            select(PipelineStage)
            .join(Pipeline, PipelineStage.pipeline_id == Pipeline.id)
            .where(Pipeline.business_id == business_id)
            .order_by(PipelineStage.position)
        ).all()
    )


def update_pipeline_stage(session: Session, stage: PipelineStage) -> PipelineStage:
    session.add(stage)
    session.commit()
    session.refresh(stage)
    return stage

