"""update_invoice — selective patch of an invoice's metadata + status.

Locked   : invoice_id  (the LLM resolves; the human cannot retarget)
Editable : issued_date, due_date, status  (all optional, selective patch)

LIMITATION (intentional, v1): this capability does NOT edit line items.
Item editing requires a list-of-objects input shape the capability format
does not support yet, and is also pre-approval only per the service.
Item edits will arrive together with the create_invoice work (Batch 5
second half) once `InputSpec` gains a list_of kind.

Tenant scope: invoice is Direct-tenanted. Validate reads via the read
model; out-of-tenant invoice returns ZERO rows → clean 'not found' refusal.

Selective patch: native via Pydantic. The engine only puts user-supplied
fields into `inputs`; `_execute` splats them into `InvoiceUpdateData`,
which the service `model_dump(exclude_unset=True)`s — absent fields are
absent from the UPDATE and the DB column stays at its current value.

Status transitions are the careful bit. The SERVICE is the single source
of truth for the state machine (DRAFT → SENT/APPROVED, SENT → APPROVED,
PARTIAL/APPROVED → nothing via this path; PAID/CANCELLED invoices refuse
edits entirely). We do NOT re-implement it here. An illegal transition
surfaces as EXECUTE_FAILED at commit with the service's exact reason —
the user must prepare again with a legal target.

What we DO do here: show the transition explicitly in the preview. A
user confirming a status change must see old → new clearly so they know
exactly what they're approving.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.models.enums import InvoiceStatus
from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.services.invoice_service import (
    InvoiceUpdateData,
    InvoiceUpdateWithItems,
    update_invoice as service_update_invoice,
)
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("invoice_id", "uuid", required=True, locked=True),
    InputSpec("issued_date", "date", required=False, locked=False),
    InputSpec("due_date", "date", required=False, locked=False),
    InputSpec(
        "status", "enum", required=False, locked=False,
        # Full enum — the service's state machine is the gate. Restricting
        # the input enum would silently hide failure cases instead of letting
        # the user see a clear EXECUTE_FAILED with the service's reason.
        enum_values=tuple(s.value for s in InvoiceStatus),
    ),
)

_EDITABLE_NAMES = tuple(s.name for s in _INPUTS if not s.locked)


def _load_invoice(session: Session, user: User, invoice_id: Any) -> dict[str, Any]:
    cq = compile_query(
        ReadQuery(
            "invoices",
            filters=[Filter("id", "=", str(invoice_id))],
            select=[
                "invoice_number", "status", "issued_date", "due_date",
                "total_amount", "customer_name", "lead_title",
            ],
        ),
        business_id=user.business_id,
    )
    rows = execute_query(cq, session).rows
    if not rows:
        raise ValueError(f"invoice {invoice_id} not found for this business")
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

    current = _load_invoice(session, user, inputs["invoice_id"])

    changes: list[str] = []
    for name, new_value in provided.items():
        old_value = current.get(name)
        # `new_value` comes from the engine post-coercion (date as datetime.date,
        # status as the enum's str value). Read-model returns dates as iso
        # strings and status as the enum's str value too. Compare stringified.
        if str(old_value) == str(new_value):
            continue
        changes.append(f"{name}: {_fmt(old_value)} → {_fmt(new_value)}")

    invoice_number = current.get("invoice_number") or "(no number)"
    owner = current.get("customer_name") or current.get("lead_title") or "(unlinked)"

    if not changes:
        return (
            f"Update invoice {invoice_number!r} for {owner}: "
            f"(no changes — all provided values match current)."
        )

    # Mention the status transition more emphatically when it's in the patch —
    # this is the highest-risk field on this capability.
    has_status_change = any(c.startswith("status:") for c in changes)
    header = (
        f"Update invoice {invoice_number!r} for {owner}"
        + (" (STATUS TRANSITION)" if has_status_change else "")
        + f" — {len(changes)} change(s):"
    )
    return header + "\n  " + "\n  ".join(changes)


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    patch = {k: v for k, v in inputs.items() if k in _EDITABLE_NAMES}
    # status arrives as a string from the engine's enum coercion; the DTO
    # accepts the enum or its value. Be explicit so the type contract is
    # readable downstream.
    if "status" in patch and isinstance(patch["status"], str):
        patch["status"] = InvoiceStatus(patch["status"])

    invoice = service_update_invoice(
        session=session,
        current_user=user,
        invoice_id=inputs["invoice_id"],
        data=InvoiceUpdateWithItems(
            invoice=InvoiceUpdateData(**patch),
            items=None,    # item editing deferred to create_invoice work
        ),
    )
    return {
        "invoice_id": str(invoice.id),
        "invoice_number": invoice.invoice_number,
        "status": invoice.status.value,
        "updated_fields": sorted(patch.keys()),
    }


DECLARATION = CapabilityDeclaration(
    name="update_invoice",
    description=(
        "Update an invoice's issued_date, due_date, or status. Selective "
        "patch — only fields you supply are changed. The service enforces "
        "the status state machine (e.g. DRAFT → SENT, SENT → APPROVED); "
        "illegal transitions are rejected at commit. Paid and cancelled "
        "invoices refuse all edits. v1: this capability does NOT edit line "
        "items — that arrives with the create_invoice work."
    ),
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
