"""complete_followup — mark a pending follow-up as done.

Locked   : followup_id   (the LLM resolves; the human cannot retarget)
Editable : note          (optional closing note; overwrites the existing note when supplied)

Tenant scope: lead_followups is ViaParent-tenanted (no own business_id). The
validate hook reads the follow-up through the read model — a row belonging
to another business returns ZERO rows, which becomes a clean refusal.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.models.lead_followup import LeadFollowupDone
from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.services.lead_followup_service import mark_followup_done as service_mark_followup_done
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("followup_id", "uuid", required=True, locked=True),
    InputSpec("note", "string", required=False, locked=False),
)


def _load_followup(session: Session, user: User, followup_id: UUID) -> dict[str, Any]:
    """Tenant-scoped read of the follow-up + its lead title. Raises ValueError
    with a clean 'not found' message if the follow-up does not belong to this
    business (the read model returns zero rows ViaParent-scoped via leads)."""
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
    if fu["status"] != "pending":
        # Mirror the service's terminal-state rule so the LLM sees the reason
        # at prepare time rather than via EXECUTE_FAILED on commit.
        raise ValueError(
            f"follow-up is {fu['status']!r}; only pending follow-ups can be marked done"
        )
    note = inputs.get("note")
    base = f"Mark follow-up done on lead {fu['lead_title']!r} (scheduled {fu['scheduled_at']})"
    return f"{base} — note: {note}." if note else f"{base}."


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    followup = service_mark_followup_done(
        session=session,
        current_user=user,
        followup_id=inputs["followup_id"],
        data=LeadFollowupDone(note=inputs.get("note")),
    )
    return {
        "followup_id": str(followup.id),
        "lead_id": str(followup.lead_id),
        "status": followup.status,
        "completed_at": followup.completed_at.isoformat() if followup.completed_at else None,
    }


DECLARATION = CapabilityDeclaration(
    name="complete_followup",
    description="Mark a pending follow-up as done.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
