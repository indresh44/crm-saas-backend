"""add_lead_note capability — write surface declaration.

Locked   : lead_id     (the LLM picks the lead)
Editable : description (the human may rewrite the note text)

CRITICAL: the activity type is HARD-CODED to LeadActivityType.NOTE here. There
is NO `type` input. The generic lead_activity_service.create_activity will
accept any LeadActivityType on create (audit-flagged), but every code path that
ever flows through THIS capability writes type=NOTE. System-only types
(STATUS_CHANGE, PAYMENT_*) can never be forged via the write surface.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.models.enums import LeadActivityType
from app.models.lead import LeadActivityCreate
from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.services.lead_activity_service import create_activity
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("lead_id", "uuid", required=True, locked=True),
    InputSpec("description", "string", required=True, locked=False),
)

_PREVIEW_SNIPPET = 60   # chars of the note shown in the preview


def _validate(session: Session, user: User, inputs: dict[str, Any]) -> str:
    """Confirm the lead exists for this business; build a short preview."""
    lead_id = inputs["lead_id"]
    description = inputs["description"].strip()
    if not description:
        raise ValueError("note description cannot be empty")

    cq = compile_query(
        ReadQuery(
            "leads",
            filters=[Filter("id", "=", str(lead_id))],
            select=["title"],
        ),
        business_id=user.business_id,
    )
    rows = execute_query(cq, session).rows
    if not rows:
        raise ValueError(f"lead {lead_id} not found for this business")

    snippet = description if len(description) <= _PREVIEW_SNIPPET else description[:_PREVIEW_SNIPPET].rstrip() + "…"
    return f"Add note to lead '{rows[0]['title']}': {snippet}"


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    description = inputs["description"].strip()
    if not description:
        # Belt + braces: the validate hook checks this at prepare, but the user
        # could blank the editable field at commit. The generic create_activity
        # service doesn't reject empty descriptions on create.
        raise ValueError("note description cannot be empty")

    activity = create_activity(
        session=session,
        current_user=user,
        lead_id=inputs["lead_id"],
        data=LeadActivityCreate(
            type=LeadActivityType.NOTE,   # hard-coded — never read from inputs
            description=description,
        ),
    )
    return {
        "activity_id": str(activity.id),
        "lead_id": str(activity.lead_id),
        "description": activity.description,
    }


DECLARATION = CapabilityDeclaration(
    name="add_lead_note",
    description="Append a free-text note to a lead's activity log.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
