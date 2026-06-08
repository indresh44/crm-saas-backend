"""cancel_followup — mark a pending follow-up as cancelled.

Locked   : followup_id   (the LLM resolves; the human cannot retarget)
Editable : note          (optional reason/closing note)

Tenant scope: lead_followups is ViaParent-tenanted; the validate hook reads
through the read model so an out-of-tenant follow-up returns ZERO rows and
becomes a clean refusal.

The service refuses to cancel done/cancelled follow-ups; we mirror that check
in validate() so the LLM sees the reason at prepare time, not via
EXECUTE_FAILED on commit.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.models.lead_followup import LeadFollowupCancel
from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.services.lead_followup_service import cancel_followup as service_cancel_followup
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("followup_id", "uuid", required=True, locked=True),
    InputSpec("note", "string", required=False, locked=False),
)


def _load_followup(session: Session, user: User, followup_id: UUID) -> dict[str, Any]:
    cq = compile_query(
        ReadQuery(
            "lead_followups",
            filters=[Filter("id", "=", str(followup_id))],
            select=["id", "scheduled_at", "status", "note", "lead_title"],
        ),
        business_id=user.business_id,
    )
    rows = execute_query(cq, session).rows
    if not rows:
        raise ValueError(f"follow-up {followup_id} not found for this business")
    return rows[0]


def _validate(session: Session, user: User, inputs: dict[str, Any]) -> str:
    fu = _load_followup(session, user, inputs["followup_id"])
    if fu["status"] in ("done", "cancelled"):
        raise ValueError(
            f"follow-up is already {fu['status']!r}; nothing to cancel"
        )
    note = inputs.get("note")
    base = f"Cancel follow-up on lead {fu['lead_title']!r} (scheduled {fu['scheduled_at']})"
    return f"{base} — reason: {note}." if note else f"{base}."


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    followup = service_cancel_followup(
        session=session,
        current_user=user,
        followup_id=inputs["followup_id"],
        data=LeadFollowupCancel(note=inputs.get("note")),
    )
    return {
        "followup_id": str(followup.id),
        "lead_id": str(followup.lead_id),
        "status": followup.status,
    }


DECLARATION = CapabilityDeclaration(
    name="cancel_followup",
    description="Cancel a pending follow-up.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
