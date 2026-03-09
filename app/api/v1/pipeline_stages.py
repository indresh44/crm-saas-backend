from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.pipeline import PipelineStage, PipelineStageRead
from app.models.user import User
from app.services.pipeline_service import (
    PipelineStageCreateForUser,
    PipelineStageUpdate,
    create_stage as service_create_stage,
    list_stages_for_pipeline_for_user,
    update_stage as service_update_stage,
)

router = APIRouter()


@router.get("/pipelines/{pipeline_id}/stages", response_model=List[PipelineStageRead])
def list_stages(
    pipeline_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[PipelineStageRead]:
    stages = list_stages_for_pipeline_for_user(
        session=session,
        current_user=current_user,
        pipeline_id=pipeline_id,
    )
    return stages


@router.post("/pipeline-stages", response_model=PipelineStageRead)
def create_pipeline_stage(
    payload: PipelineStageCreateForUser,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PipelineStageRead:
    stage = service_create_stage(
        session=session,
        current_user=current_user,
        data=payload,
    )
    return stage


@router.patch("/pipeline-stages/{stage_id}", response_model=PipelineStageRead)
def update_pipeline_stage(
    stage_id: UUID,
    payload: PipelineStageUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PipelineStageRead:
    stage = service_update_stage(
        session=session,
        current_user=current_user,
        stage_id=stage_id,
        data=payload,
    )
    return stage

