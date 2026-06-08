"""update_lead — selective patch of a lead's editable fields.

Locked   : lead_id (the LLM resolves; the human cannot retarget)
Editable : title, notes, estimated_value, source, service_date,
           follow_up_at, assigned_to — all optional; absent fields are NOT
           touched (PATCH semantics, not PUT)

Selective patch is supported NATIVELY by the underlying service via
`LeadUpdate(...).model_dump(exclude_unset=True)`. The engine's validator
only puts fields the user actually supplied into `inputs`, so splatting
inputs into the DTO preserves the contract — fields absent from inputs are
absent from the DTO, are NOT in the `exclude_unset` dump, and the service
leaves the column alone. No execute-side "fetch current + apply provided"
adapter needed.

Tenant scoping: lead is a Direct-tenanted entity (has business_id). The
validate hook reads through the read model — a lead from another business
returns ZERO rows, becoming a clean 'not found' refusal.

Preview: shows old → new for each changed field so the user can confirm
WHAT will change, not just THAT something will change.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.models.enums import LeadSource
from app.models.lead import LeadUpdate
from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.services.lead_service import update_lead as service_update_lead
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("lead_id", "uuid", required=True, locked=True),
    InputSpec("title", "string", required=False, locked=False),
    InputSpec("notes", "string", required=False, locked=False),
    InputSpec("estimated_value", "decimal", required=False, locked=False),
    InputSpec(
        "source", "enum", required=False, locked=False,
        enum_values=tuple(s.value for s in LeadSource),
    ),
    InputSpec("service_date", "date", required=False, locked=False),
    InputSpec("follow_up_at", "datetime", required=False, locked=False),
    InputSpec("assigned_to", "uuid", required=False, locked=False),
)

# Names that may appear in `inputs` and represent ACTUAL editable column
# patches (i.e. everything except the LOCKED id). Kept here so the validate
# and execute hooks agree on what counts as "an editable field".
_EDITABLE_NAMES = tuple(s.name for s in _INPUTS if not s.locked)


def _load_lead(session: Session, user: User, lead_id: UUID) -> dict[str, Any]:
    """Tenant-scoped read of the lead's current values. Raises ValueError if
    the lead doesn't belong to this business (the read returns zero rows)."""
    cq = compile_query(
        ReadQuery(
            "leads",
            filters=[Filter("id", "=", str(lead_id))],
            select=[
                "id", "title", "notes", "estimated_value", "source",
                "service_date", "follow_up_at", "assigned_to",
            ],
        ),
        business_id=user.business_id,
    )
    rows = execute_query(cq, session).rows
    if not rows:
        raise ValueError(f"lead {lead_id} not found for this business")
    return rows[0]


def _fmt(value: Any) -> str:
    """One-line, human-readable rendering for the preview. None -> '(blank)',
    Decimal/UUID/date/datetime -> str(value); strings shown verbatim."""
    if value is None or value == "":
        return "(blank)"
    return str(value)


def _validate(session: Session, user: User, inputs: dict[str, Any]) -> str:
    # The engine has already stripped unknown/locked fields from `inputs` and
    # coerced types. What's left is exactly what the user wants to patch.
    provided = {k: v for k, v in inputs.items() if k in _EDITABLE_NAMES}
    if not provided:
        raise ValueError(
            "no fields to update — provide at least one of: "
            + ", ".join(_EDITABLE_NAMES)
        )

    current = _load_lead(session, user, inputs["lead_id"])

    # Show old → new for fields that actually change; quietly skip identical
    # values. A patch with only same-value fields is still a valid no-op
    # commit (the service is idempotent); just flag it in the preview.
    changes: list[str] = []
    for name, new_value in provided.items():
        old_value = current.get(name)
        # Normalise both sides to comparable forms — the read model returns
        # decimals/uuids as strings, but `inputs` carries them as their typed
        # values. Stringify both sides for the comparison only (display uses
        # _fmt which is also stringy).
        if str(old_value) == str(new_value):
            continue
        changes.append(f"{name}: {_fmt(old_value)} → {_fmt(new_value)}")

    title = current.get("title") or "(untitled)"
    if not changes:
        return f"Update lead {title!r}: (no changes — all provided values match current)."
    return f"Update lead {title!r} — {len(changes)} change(s):\n  " + "\n  ".join(changes)


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    # Build the DTO from ONLY the fields the user supplied. Pydantic marks
    # them as "set"; service.update_lead does model_dump(exclude_unset=True)
    # so unsupplied columns remain untouched in the DB.
    patch = {k: v for k, v in inputs.items() if k in _EDITABLE_NAMES}
    # `source` arrives as the enum's str value (engine coerces enum input to
    # string); LeadUpdate accepts the enum or its value, but be explicit.
    if "source" in patch and isinstance(patch["source"], str):
        patch["source"] = LeadSource(patch["source"])
    lead = service_update_lead(
        session=session,
        current_user=user,
        lead_id=inputs["lead_id"],
        data=LeadUpdate(**patch),
    )
    return {
        "lead_id": str(lead.id),
        "title": lead.title,
        "updated_fields": sorted(patch.keys()),
    }


DECLARATION = CapabilityDeclaration(
    name="update_lead",
    description="Update a lead's editable fields. Selective patch — only "
                "fields you supply are changed; others stay as they are.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
