"""create_lead capability — write surface declaration.

Locked   : stage_id, title, customer_id, source
Editable : estimated_value, notes

NOTE on the audit-flagged gap: lead_service.create_lead forwards `customer_id`
(and `assigned_to`) without verifying they belong to the caller's business. We
DO NOT silently fix that here — the fix belongs in the service so every caller
benefits. Tracked as the audit-level TODO.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.models.enums import LeadSource
from app.models.lead import LeadCreate
from app.models.user import User
from app.repositories.lead_repository import get_pipeline_stage_by_id
from app.services.lead_service import create_lead as service_create_lead
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("stage_id", "uuid", required=True, locked=True),
    InputSpec("title", "string", required=True, locked=True),
    InputSpec("customer_id", "uuid", required=False, locked=True),
    InputSpec(
        "source", "enum", required=False, locked=True,
        enum_values=tuple(s.value for s in LeadSource),
    ),
    InputSpec("estimated_value", "decimal", required=False, locked=False),
    InputSpec("notes", "string", required=False, locked=False),
)


def _validate(session: Session, user: User, inputs: dict[str, Any]) -> str:
    """Confirm the stage belongs to the caller's business; build preview.

    Stages are not an exposed read-model entity, so we use the repository's
    existing scoped lookup (same gate the service uses). The customer_id /
    assigned_to scoping gap is deliberately NOT addressed here — see module
    docstring.
    """
    stage_id = inputs["stage_id"]
    stage = get_pipeline_stage_by_id(
        session=session, business_id=user.business_id, stage_id=stage_id,
    )
    if stage is None:
        raise ValueError(f"stage {stage_id} does not belong to this business")

    title = inputs["title"]
    return f"New enquiry: '{title}' in stage {stage.name}."


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    source_value = inputs.get("source")
    lead = service_create_lead(
        session=session,
        current_user=user,
        data=LeadCreate(
            stage_id=inputs["stage_id"],
            title=inputs["title"],
            customer_id=inputs.get("customer_id"),
            source=LeadSource(source_value) if source_value is not None else None,
            estimated_value=inputs.get("estimated_value"),
            notes=inputs.get("notes"),
        ),
    )
    return {
        "lead_id": str(lead.id),
        "title": lead.title,
        "stage_id": str(lead.stage_id),
    }


DECLARATION = CapabilityDeclaration(
    name="create_lead",
    description="Create a new lead in a pipeline stage.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
