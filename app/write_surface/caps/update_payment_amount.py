"""update_payment_amount — VOID-AND-REPLACE correction of a payment's amount.

Locked   : payment_id     (the LLM resolves; the human cannot retarget)
Editable : amount, reason (the new amount and an optional reason for the
                           correction)

This is NOT an in-place edit. The service VOIDS the original payment
(sets voided_at / voided_reason / voided_by) and CREATES a new payment
row with the corrected amount and `replaces_payment_id` linked back to
the old one. The audit chain is preserved.

The preview MUST communicate this honestly — the human is approving a
void-and-replace, not a silent number edit. Done deliberately in the
wording.

Tenant scope: payments is Direct-tenanted. Validate reads through the
read model — out-of-tenant payment returns ZERO rows -> clean refusal.

Service guards (NOT re-implemented):
  * payment is voided                    -> 400 -> EXECUTE_FAILED
  * payment is on a cancelled invoice    -> 400 -> EXECUTE_FAILED
  * amount <= 0                          -> 400 -> EXECUTE_FAILED
  * amount > remaining capacity          -> 400 -> EXECUTE_FAILED
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.models.payment import PaymentAmountUpdate
from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.services.payment_service import update_payment_amount as service_update_payment_amount
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("payment_id", "uuid", required=True, locked=True),
    InputSpec("amount", "decimal", required=True, locked=False),
    InputSpec("reason", "string", required=False, locked=False),
)


def _fmt_money(amount: Decimal) -> str:
    return f"₹{amount:,.2f}"


def _validate(session: Session, user: User, inputs: dict[str, Any]) -> str:
    payment_id = inputs["payment_id"]
    new_amount: Decimal = inputs["amount"]

    pcq = compile_query(
        ReadQuery(
            "payments",
            filters=[Filter("id", "=", str(payment_id))],
            select=["id", "amount", "payment_method", "invoice_id",
                    "invoice_number", "is_voided"],
        ),
        business_id=user.business_id,
    )
    prows = execute_query(pcq, session).rows
    if not prows:
        raise ValueError(f"payment {payment_id} not found for this business")
    payment = prows[0]

    if payment.get("is_voided"):
        raise ValueError(
            f"payment {payment_id} is voided; amount cannot be edited"
        )

    icq = compile_query(
        ReadQuery(
            "invoices",
            filters=[Filter("id", "=", str(payment["invoice_id"]))],
            select=["invoice_number", "balance"],
        ),
        business_id=user.business_id,
    )
    irows = execute_query(icq, session).rows
    invoice_number = payment.get("invoice_number") or (
        irows[0].get("invoice_number") if irows else "(unknown)"
    )
    current_balance = Decimal(irows[0]["balance"]) if irows else Decimal("0")
    old_amount = Decimal(str(payment["amount"]))
    # Void restores `old_amount` to balance; new payment subtracts `new_amount`.
    # Net: new_balance = current_balance + old_amount - new_amount.
    new_balance = current_balance + old_amount - new_amount

    reason = (inputs.get("reason") or "").strip()
    base = (
        f"CORRECT payment amount on invoice {invoice_number!r}: "
        f"void the current {_fmt_money(old_amount)} payment AND "
        f"record a new payment of {_fmt_money(new_amount)} in its place "
        f"(replaces_payment_id audit chain preserved). "
        f"Invoice balance: {_fmt_money(current_balance)} → {_fmt_money(new_balance)}."
    )
    return f"{base} Reason: {reason}." if reason else base


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    # The service returns the NEW (replacement) payment.
    replacement = service_update_payment_amount(
        session=session,
        current_user=user,
        payment_id=inputs["payment_id"],
        data=PaymentAmountUpdate(
            amount=inputs["amount"],
            reason=inputs.get("reason"),
        ),
    )
    return {
        "new_payment_id": str(replacement.id),
        "replaces_payment_id": str(replacement.replaces_payment_id)
            if replacement.replaces_payment_id else None,
        "invoice_id": str(replacement.invoice_id),
        "amount": str(replacement.amount),
    }


DECLARATION = CapabilityDeclaration(
    name="update_payment_amount",
    description="Correct a payment amount via VOID-AND-REPLACE: the original "
                "payment is voided and a new payment with the corrected "
                "amount is created, linked to the old one via "
                "replaces_payment_id. The audit chain is preserved. "
                "Service rejects amounts > 0 or > remaining capacity.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
