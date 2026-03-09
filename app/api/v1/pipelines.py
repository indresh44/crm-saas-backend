from typing import Any, Dict, List
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.pipeline import PipelineCreate, PipelineRead
from app.models.user import User
from app.services.pipeline_service import (
    create_pipeline as service_create_pipeline,
    get_pipeline as service_get_pipeline,
    list_pipelines as service_list_pipelines,
)
from app.services.pipeline_board_service import get_pipeline_board as service_get_pipeline_board

router = APIRouter()


@router.get("/pipelines", response_model=List[PipelineRead])
def list_pipelines(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[PipelineRead]:
    pipelines = service_list_pipelines(session=session, current_user=current_user)
    return pipelines


@router.post("/pipelines", response_model=PipelineRead)
def create_pipeline(
    payload: PipelineCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PipelineRead:
    pipeline = service_create_pipeline(session=session, current_user=current_user, data=payload)
    return pipeline


@router.get("/pipelines/{pipeline_id}", response_model=PipelineRead)
def get_pipeline(
    pipeline_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PipelineRead:
    pipeline = service_get_pipeline(session=session, current_user=current_user, pipeline_id=pipeline_id)
    return pipeline


@router.get("/pipelines/{pipeline_id}/board")
def get_pipeline_board(
    pipeline_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Dict[str, List[Dict[str, Any]]]:
    return service_get_pipeline_board(
        session=session,
        current_user=current_user,
        pipeline_id=pipeline_id,
    )

