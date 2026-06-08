"""cancel_invoice — flip an invoice's status to CANCELLED.

Locked   : invoice_id   (the LLM resolves; the human cannot retarget)
Editable : reason       (optional free-text reason)

Tenant scope: invoice is Direct-tenanted. The validate hook reads through
the read model, so an out-of-tenant invoice returns ZERO rows and becomes
a clean 'not found' refusal.

Service guards (NOT re-implemented here — single source of truth):
  * status must be draft / sent / approved
    (partial / paid / already-cancelled are refused)
  * no payments may exist on the invoice (defence-in-depth in the service)
  * PDF cache is cleared

Refusal at commit time (e.g. payments recorded between prepare and commit)
surfaces as EXECUTE_FAILED — the prepared action is already consumed and
the user must prepare again. Pattern-consistent with record_payment.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.services.invoice_service import cancel_invoice as service_cancel_invoice
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("invoice_id", "uuid", required=True, locked=True),
    InputSpec("reason", "string", required=False, locked=False),
)


def _validate(session: Session, user: User, inputs: dict[str, Any]) -> str:
    cq = compile_query(
        ReadQuery(
            "invoices",
            filters=[Filter("id", "=", str(inputs["invoice_id"]))],
            select=[
                "invoice_number", "status", "total_amount",
                "customer_name", "lead_title",
            ],
        ),
        business_id=user.business_id,
    )
    rows = execute_query(cq, session).rows
    if not rows:
        raise ValueError(f"invoice {inputs['invoice_id']} not found for this business")
    row = rows[0]

    # Owner of the invoice — prefer customer name (via two-hop), fall back to
    # the lead title. An invoice with neither linkage shows '(unlinked)'.
    owner = row.get("customer_name") or row.get("lead_title") or "(unlinked)"
    status = row.get("status") or "?"
    total = row.get("total_amount") or "0"

    reason = (inputs.get("reason") or "").strip()
    base = (
        f"CANCEL invoice {row.get('invoice_number')!r} "
        f"for {owner} — current status {status!r}, total ₹{total}. "
        f"After commit: status becomes 'cancelled' and the cached PDF is cleared."
    )
    return f"{base} Reason: {reason}." if reason else base


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    invoice = service_cancel_invoice(
        session=session,
        current_user=user,
        invoice_id=inputs["invoice_id"],
        reason=inputs.get("reason"),
    )
    return {
        "invoice_id": str(invoice.id),
        "invoice_number": invoice.invoice_number,
        "status": invoice.status.value,
        "cancelled_at": invoice.cancelled_at.isoformat() if invoice.cancelled_at else None,
    }


DECLARATION = CapabilityDeclaration(
    name="cancel_invoice",
    description="Cancel an invoice (state change to CANCELLED). Allowed on "
                "draft / sent / approved with no payments. The reason field "
                "is optional but recommended for audit clarity.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
