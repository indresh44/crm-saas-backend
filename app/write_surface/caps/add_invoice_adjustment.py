"""add_invoice_adjustment — record a discount or write-off on an invoice.

Locked   : invoice_id, adjustment_type   (the LLM proposes; the human cannot
                                          retarget the invoice or switch the
                                          adjustment kind at confirm time)
Editable : amount, reason                (the human may adjust at confirm)

Tenant scope: invoice is Direct-tenanted. The validate hook reads through
the read model — out-of-tenant invoice returns ZERO rows and becomes a
clean 'not found' refusal.

Service guards (NOT re-implemented here — single source of truth):
  * status must be sent / approved / partial
    (draft / paid / cancelled are refused)
  * amount must not exceed the remaining balance
  * PDF cache is cleared, invoice status is recomputed afterwards

The preview shows where the BALANCE lands after the adjustment — mirrors
record_payment's preview shape because an adjustment, like a payment,
changes how much the customer owes.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlmodel import Session

from app.models.enums import InvoiceAdjustmentType
from app.models.invoice_adjustment import InvoiceAdjustmentCreate
from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.services.invoice_adjustment_service import add_adjustment as service_add_adjustment
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("invoice_id", "uuid", required=True, locked=True),
    InputSpec(
        "adjustment_type", "enum", required=True, locked=True,
        enum_values=tuple(a.value for a in InvoiceAdjustmentType),
    ),
    InputSpec("amount", "decimal", required=True, locked=False),
    InputSpec("reason", "string", required=False, locked=False),
)


def _fmt_money(amount: Decimal) -> str:
    return f"₹{amount:,.2f}"


def _validate(session: Session, user: User, inputs: dict[str, Any]) -> str:
    invoice_id = inputs["invoice_id"]
    amount: Decimal = inputs["amount"]
    adj_type: str = inputs["adjustment_type"]   # 'discount' | 'write_off'

    cq = compile_query(
        ReadQuery(
            "invoices",
            filters=[Filter("id", "=", str(invoice_id))],
            select=[
                "invoice_number", "status", "total_amount", "balance",
                "is_cancelled",
            ],
        ),
        business_id=user.business_id,
    )
    rows = execute_query(cq, session).rows
    if not rows:
        raise ValueError(f"invoice {invoice_id} not found for this business")
    row = rows[0]

    if row.get("is_cancelled"):
        raise ValueError(
            f"invoice {row.get('invoice_number')!r} is cancelled — adjustments are refused"
        )

    current_balance = Decimal(row["balance"])
    # An adjustment reduces what the customer owes (signed: balance > 0 means
    # outstanding). The new balance = old − adjustment. The service refuses
    # negative-balance overshoots, but we show the projection here either way
    # so the user sees the impact before confirming.
    new_balance = current_balance - amount

    reason = (inputs.get("reason") or "").strip()
    base = (
        f"Add {adj_type.upper()} adjustment of {_fmt_money(amount)} to invoice "
        f"{row.get('invoice_number')!r} "
        f"(current status {row.get('status')!r}; current balance {_fmt_money(current_balance)}, "
        f"new balance {_fmt_money(new_balance)})."
    )
    return f"{base} Reason: {reason}." if reason else base


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    adjustment = service_add_adjustment(
        session=session,
        current_user=user,
        invoice_id=inputs["invoice_id"],
        data=InvoiceAdjustmentCreate(
            amount=inputs["amount"],
            adjustment_type=InvoiceAdjustmentType(inputs["adjustment_type"]),
            reason=inputs.get("reason"),
        ),
    )
    return {
        "adjustment_id": str(adjustment.id),
        "invoice_id": str(adjustment.invoice_id),
        "amount": str(adjustment.amount),
        "adjustment_type": adjustment.adjustment_type,
    }


DECLARATION = CapabilityDeclaration(
    name="add_invoice_adjustment",
    description="Add a discount or write-off adjustment to an invoice. "
                "Reduces the outstanding balance. Allowed on sent / approved "
                "/ partial invoices; service rejects amounts that would "
                "drive the balance negative.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
