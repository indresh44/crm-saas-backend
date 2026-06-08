"""create_followup — schedule a follow-up on a lead.

Locked   : lead_id              (the LLM resolves; the human cannot retarget)
Editable : scheduled_at, note   (the human may adjust at confirm)

Tenant scope: lead_followups is ViaParent-tenanted (no own business_id), so
the validate hook verifies the lead exists in THIS business via the read
model — a read against `leads` filtered by id returns ZERO rows for an
out-of-tenant lead, which becomes the standard 'not found' refusal.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session

from app.models.lead_followup import LeadFollowupCreate
from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.services.lead_followup_service import create_followup as service_create_followup
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("lead_id", "uuid", required=True, locked=True),
    InputSpec("scheduled_at", "datetime", required=True, locked=False),
    InputSpec("note", "string", required=False, locked=False),
)


def _validate(session: Session, user: User, inputs: dict[str, Any]) -> str:
    """Confirm the lead exists in THIS business (via the read model — tenant-
    scoped). Build the preview."""
    lead_id = inputs["lead_id"]
    scheduled_at: datetime = inputs["scheduled_at"]
    note = inputs.get("note")

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
    title = rows[0]["title"]

    suffix = f" — {note}" if note else ""
    return f"Schedule follow-up for lead {title!r} on {scheduled_at.isoformat()}{suffix}."


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    followup = service_create_followup(
        session=session,
        current_user=user,
        data=LeadFollowupCreate(
            lead_id=inputs["lead_id"],
            scheduled_at=inputs["scheduled_at"],
            note=inputs.get("note"),
        ),
    )
    return {
        "followup_id": str(followup.id),
        "lead_id": str(followup.lead_id),
        "scheduled_at": followup.scheduled_at.isoformat(),
        "status": followup.status,
    }


DECLARATION = CapabilityDeclaration(
    name="create_followup",
    description="Schedule a follow-up on a lead.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
