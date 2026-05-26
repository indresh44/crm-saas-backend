"""update_payment_metadata — in-place edit of payment_date / payment_method /
reference. NO money math: the amount is not touched, so balance is unchanged.

Locked   : payment_id (the LLM resolves; the human cannot retarget)
Editable : payment_date, payment_method, reference (all optional, selective
           patch — omitted fields are NOT touched)

Selective-patch contract: the service uses `if data.X is not None` checks
(NOT Pydantic `exclude_unset`) to decide what to update. Passing `None` to
the DTO is treated by the service as 'not supplied'. The capability
forwards only the keys the user actually supplied, so the DTO's other
fields default to None and the service leaves the column alone.

Tenant scope: payments is Direct-tenanted. Validate reads through the read
model — out-of-tenant payment returns ZERO rows -> clean refusal.

Service guards (NOT re-implemented):
  * payment is voided                          -> 400 -> EXECUTE_FAILED
  * payment is on a cancelled invoice          -> 400 -> EXECUTE_FAILED
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.models.enums import PaymentMethod
from app.models.payment import PaymentMetadataUpdate
from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.services.payment_service import update_payment_metadata as service_update_payment_metadata
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("payment_id", "uuid", required=True, locked=True),
    InputSpec("payment_date", "date", required=False, locked=False),
    InputSpec(
        "payment_method", "enum", required=False, locked=False,
        enum_values=tuple(m.value for m in PaymentMethod),
    ),
    InputSpec("reference", "string", required=False, locked=False),
)

_EDITABLE_NAMES = tuple(s.name for s in _INPUTS if not s.locked)


def _load_payment(session: Session, user: User, payment_id: UUID) -> dict[str, Any]:
    cq = compile_query(
        ReadQuery(
            "payments",
            filters=[Filter("id", "=", str(payment_id))],
            select=["id", "payment_date", "payment_method", "reference",
                    "invoice_number", "is_voided"],
        ),
        business_id=user.business_id,
    )
    rows = execute_query(cq, session).rows
    if not rows:
        raise ValueError(f"payment {payment_id} not found for this business")
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

    current = _load_payment(session, user, inputs["payment_id"])

    if current.get("is_voided"):
        raise ValueError(
            f"payment {inputs['payment_id']} is voided; metadata cannot be edited"
        )

    changes: list[str] = []
    for name, new_value in provided.items():
        old_value = current.get(name)
        if str(old_value) == str(new_value):
            continue
        changes.append(f"{name}: {_fmt(old_value)} → {_fmt(new_value)}")

    invoice_number = current.get("invoice_number") or "(unknown invoice)"
    if not changes:
        return (
            f"Update payment metadata on invoice {invoice_number!r}: "
            f"(no changes — all provided values match current)."
        )
    header = (
        f"Update payment metadata on invoice {invoice_number!r} "
        f"— {len(changes)} change(s) (amount unchanged; balance unaffected):"
    )
    return header + "\n  " + "\n  ".join(changes)


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    patch = {k: v for k, v in inputs.items() if k in _EDITABLE_NAMES}
    if "payment_method" in patch and isinstance(patch["payment_method"], str):
        patch["payment_method"] = PaymentMethod(patch["payment_method"])
    payment = service_update_payment_metadata(
        session=session,
        current_user=user,
        payment_id=inputs["payment_id"],
        data=PaymentMetadataUpdate(**patch),
    )
    return {
        "payment_id": str(payment.id),
        "invoice_id": str(payment.invoice_id),
        "payment_date": payment.payment_date.isoformat(),
        "payment_method": payment.payment_method.value,
        "reference": payment.reference,
        "updated_fields": sorted(patch.keys()),
    }


DECLARATION = CapabilityDeclaration(
    name="update_payment_metadata",
    description="Edit a payment's date / method / reference in place. Does "
                "NOT change the amount — balance is unaffected. Selective "
                "patch: omitted fields are left untouched.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
