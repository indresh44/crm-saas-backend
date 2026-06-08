"""void_payment — soft-void a payment, restoring the invoice balance.

Locked   : payment_id (the LLM resolves; the human cannot retarget)
Editable : reason     (free-text reason for the void)

The service marks the payment as voided (sets voided_at / voided_reason /
voided_by) — it is NOT a delete. The audit row is preserved; the payment
becomes invisible to balance / outstanding / revenue computations because
those filter on voided_at IS NULL.

Tenant scope: payments is Direct-tenanted. The validate hook reads through
the read model — out-of-tenant payment returns ZERO rows -> clean refusal.

Service guards (NOT re-implemented):
  * payment already voided -> 400 -> EXECUTE_FAILED
  * (the parent-invoice cancelled-check on update_payment_* is intentionally
    NOT present on void_payment by the service; voiding a payment on a
    cancelled invoice is allowed for cleanup workflows.)

Preview: shows amount, method, which invoice, and the projected balance
impact (balance goes UP by the voided amount).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlmodel import Session

from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.services.payment_service import void_payment as service_void_payment
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("payment_id", "uuid", required=True, locked=True),
    InputSpec("reason", "string", required=False, locked=False),
)


def _fmt_money(amount: Decimal) -> str:
    return f"₹{amount:,.2f}"


def _validate(session: Session, user: User, inputs: dict[str, Any]) -> str:
    payment_id = inputs["payment_id"]

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
        # Pre-empt the EXECUTE_FAILED for a clearer preview-time message.
        raise ValueError(
            f"payment {payment_id} is already voided; nothing to do"
        )

    # Fetch the parent invoice's current balance via the read model.
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
    amount = Decimal(str(payment["amount"]))
    new_balance = current_balance + amount   # voiding restores the balance

    reason = (inputs.get("reason") or "").strip()
    base = (
        f"VOID payment of {_fmt_money(amount)} ({payment.get('payment_method')}) "
        f"on invoice {invoice_number!r} — "
        f"current balance {_fmt_money(current_balance)}, "
        f"new balance {_fmt_money(new_balance)}. "
        f"Soft change: the payment row is preserved with voided_at set."
    )
    return f"{base} Reason: {reason}." if reason else base


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    payment = service_void_payment(
        session=session,
        current_user=user,
        payment_id=inputs["payment_id"],
        reason=inputs.get("reason"),
    )
    return {
        "payment_id": str(payment.id),
        "invoice_id": str(payment.invoice_id),
        "amount": str(payment.amount),
        "voided_at": payment.voided_at.isoformat() if payment.voided_at else None,
    }


DECLARATION = CapabilityDeclaration(
    name="void_payment",
    description="Soft-void a payment (sets voided_at). Restores the invoice "
                "balance by the voided amount. The audit row is preserved.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
