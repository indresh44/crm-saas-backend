"""reschedule_followup — move a pending follow-up to a new time.

Locked   : followup_id            (the LLM resolves; the human cannot retarget)
Editable : scheduled_at, note     (the human may adjust at confirm)

Tenant scope: lead_followups is ViaParent-tenanted; the validate hook reads
through the read model so an out-of-tenant follow-up returns ZERO rows and
becomes a clean refusal.

The service refuses to reschedule done/cancelled follow-ups; we mirror that
check in validate() so the LLM sees the reason at prepare time rather than
hitting EXECUTE_FAILED on commit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.models.lead_followup import LeadFollowupReschedule
from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.services.lead_followup_service import (
    reschedule_followup as service_reschedule_followup,
)
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("followup_id", "uuid", required=True, locked=True),
    InputSpec("scheduled_at", "datetime", required=True, locked=False),
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
            f"follow-up is {fu['status']!r}; only pending follow-ups can be rescheduled"
        )
    new_at: datetime = inputs["scheduled_at"]
    note = inputs.get("note")
    base = (
        f"Reschedule follow-up on lead {fu['lead_title']!r} "
        f"from {fu['scheduled_at']} to {new_at.isoformat()}"
    )
    return f"{base} — note: {note}." if note else f"{base}."


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    followup = service_reschedule_followup(
        session=session,
        current_user=user,
        followup_id=inputs["followup_id"],
        data=LeadFollowupReschedule(
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
    name="reschedule_followup",
    description="Reschedule a pending follow-up to a new date/time.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
