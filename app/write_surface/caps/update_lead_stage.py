"""update_lead_stage capability — write surface declaration.

Locked   : lead_id, new_stage_id        (no editable fields — the LLM proposes a
                                         specific lead/stage pair; the human can
                                         only confirm or cancel)
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.repositories.lead_repository import get_pipeline_stage_by_id
from app.services.lead_service import move_lead_stage
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("lead_id", "uuid", required=True, locked=True),
    InputSpec("new_stage_id", "uuid", required=True, locked=True),
)


def _validate(session: Session, user: User, inputs: dict[str, Any]) -> str:
    """Confirm both lead and new stage belong to the business; build preview.

    Lead lookup via the read model (tenant-scoped + brings stage_name via the
    pipeline_stage join). Stage lookup via the repository's scoped helper
    (stages are not an exposed read-model entity).
    """
    lead_id = inputs["lead_id"]
    new_stage_id = inputs["new_stage_id"]

    cq = compile_query(
        ReadQuery(
            "leads",
            filters=[Filter("id", "=", str(lead_id))],
            select=["title", "stage_name"],
        ),
        business_id=user.business_id,
    )
    rows = execute_query(cq, session).rows
    if not rows:
        raise ValueError(f"lead {lead_id} not found for this business")
    current_title = rows[0]["title"]
    current_stage_name = rows[0].get("stage_name") or "(unknown)"

    new_stage = get_pipeline_stage_by_id(
        session=session, business_id=user.business_id, stage_id=new_stage_id,
    )
    if new_stage is None:
        raise ValueError(f"stage {new_stage_id} does not belong to this business")

    return f"Move lead '{current_title}' from {current_stage_name} → {new_stage.name}."


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    lead = move_lead_stage(
        session=session,
        current_user=user,
        lead_id=inputs["lead_id"],
        new_stage_id=inputs["new_stage_id"],
    )
    return {
        "lead_id": str(lead.id),
        "stage_id": str(lead.stage_id),
    }


DECLARATION = CapabilityDeclaration(
    name="update_lead_stage",
    description="Move a lead to a different pipeline stage.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
