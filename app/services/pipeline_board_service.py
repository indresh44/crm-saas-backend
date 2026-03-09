from typing import Any, Dict, List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.lead import LeadRead
from app.models.pipeline import PipelineStage
from app.models.user import User
from app.repositories.lead_repository import (
    get_pipeline_by_id_for_business,
    list_leads_for_business_and_stage_ids,
    list_stages_for_pipeline,
)


def get_pipeline_board(
    session: Session,
    current_user: User,
    pipeline_id: UUID,
) -> Dict[str, List[Dict[str, Any]]]:
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

    stages: List[PipelineStage] = list_stages_for_pipeline(session=session, pipeline_id=pipeline_id)
    stage_ids = [stage.id for stage in stages]

    leads = list_leads_for_business_and_stage_ids(
        session=session,
        business_id=current_user.business_id,
        stage_ids=stage_ids,
    )

    leads_by_stage: dict[UUID, list[LeadRead]] = {stage_id: [] for stage_id in stage_ids}
    for lead in leads:
        leads_by_stage.setdefault(lead.stage_id, []).append(LeadRead.model_validate(lead))

    response_stages: List[Dict[str, Any]] = []
    for stage in stages:
        response_stages.append(
            {
                "id": stage.id,
                "name": stage.name,
                "position": stage.position,
                "color": stage.color,
                "leads": leads_by_stage.get(stage.id, []),
            }
        )

    return {"stages": response_stages}

