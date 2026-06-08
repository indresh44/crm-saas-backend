"""update_customer — selective patch of a customer's editable fields.

Locked   : customer_id (the LLM resolves; the human cannot retarget)
Editable : name, phone, email, address, city, state, gst_number, notes —
           all string, all optional; absent fields are NOT touched

Selective patch is supported NATIVELY by the underlying service via
`CustomerUpdate(...).model_dump(exclude_unset=True)`. Same pattern as
update_lead — splat only the user-supplied fields into the DTO, the
service takes care of leaving the rest alone.

Phone handling is the service's responsibility (and only the service's):
when `phone` is supplied, customer_service.update_customer normalises it
AND runs a duplicate-phone check against other customers in this
business. We do NOT re-implement either here.

Tenant scoping: customer is Direct-tenanted (has business_id). The
validate hook reads through the read model — out-of-tenant customer
returns ZERO rows, becoming a clean 'not found' refusal.

Preview: old → new for each changed field.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.models.customer import CustomerUpdate
from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.services.customer_service import update_customer as service_update_customer
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("customer_id", "uuid", required=True, locked=True),
    InputSpec("name", "string", required=False, locked=False),
    InputSpec("phone", "string", required=False, locked=False),
    InputSpec("email", "string", required=False, locked=False),
    InputSpec("address", "string", required=False, locked=False),
    InputSpec("city", "string", required=False, locked=False),
    InputSpec("state", "string", required=False, locked=False),
    InputSpec("gst_number", "string", required=False, locked=False),
    InputSpec("notes", "string", required=False, locked=False),
)

_EDITABLE_NAMES = tuple(s.name for s in _INPUTS if not s.locked)


def _load_customer(session: Session, user: User, customer_id: UUID) -> dict[str, Any]:
    cq = compile_query(
        ReadQuery(
            "customers",
            filters=[Filter("id", "=", str(customer_id))],
            select=[
                "id", "name", "phone", "email", "address",
                "city", "state", "gst_number", "notes",
            ],
        ),
        business_id=user.business_id,
    )
    rows = execute_query(cq, session).rows
    if not rows:
        raise ValueError(f"customer {customer_id} not found for this business")
    return rows[0]


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "(blank)"
    return str(value)


def _validate(session: Session, user: User, inputs: dict[str, Any]) -> str:
    provided = {k: v for k, v in inputs.items() if k in _EDITABLE_NAMES}
    if not provided:
        raise ValueError(
            "no fields to update — provide at least one of: "
            + ", ".join(_EDITABLE_NAMES)
        )

    current = _load_customer(session, user, inputs["customer_id"])

    changes: list[str] = []
    for name, new_value in provided.items():
        old_value = current.get(name)
        if str(old_value) == str(new_value):
            continue
        changes.append(f"{name}: {_fmt(old_value)} → {_fmt(new_value)}")

    name = current.get("name") or "(unnamed)"
    if not changes:
        return f"Update customer {name!r}: (no changes — all provided values match current)."
    return f"Update customer {name!r} — {len(changes)} change(s):\n  " + "\n  ".join(changes)


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    patch = {k: v for k, v in inputs.items() if k in _EDITABLE_NAMES}
    customer = service_update_customer(
        session=session,
        current_user=user,
        customer_id=inputs["customer_id"],
        data=CustomerUpdate(**patch),
    )
    return {
        "customer_id": str(customer.id),
        "name": customer.name,
        "updated_fields": sorted(patch.keys()),
    }


DECLARATION = CapabilityDeclaration(
    name="update_customer",
    description="Update a customer's editable fields. Selective patch — only "
                "fields you supply are changed. Phone changes are normalised "
                "and duplicate-checked by the service.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
