"""record_payment — first capability declaration on the write surface.

Locked   : invoice_id, amount, payment_method   (the LLM proposes; the human cannot change)
Editable : payment_date, reference              (the human may adjust at confirm)

NOTE on the audit's two missing service-level checks (amount > 0, overpayment):
those are NOT silently re-implemented here. They belong on
payment_service.create_payment so every caller — HTTP, write surface, future
in-process callers — gets the same guard. Tracked separately.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlmodel import Session

from app.models.enums import PaymentMethod
from app.models.payment import PaymentCreate
from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.services.payment_service import create_payment as service_create_payment
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("invoice_id", "uuid", required=True, locked=True),
    InputSpec("amount", "decimal", required=True, locked=True),
    InputSpec(
        "payment_method", "enum", required=True, locked=True,
        enum_values=tuple(m.value for m in PaymentMethod),
    ),
    InputSpec("payment_date", "date", required=False, locked=False),
    InputSpec("reference", "string", required=False, locked=False),
)


def _fmt(amount: Decimal) -> str:
    return f"₹{amount:,.2f}"


def _validate(session: Session, user: User, inputs: dict[str, Any]) -> str:
    """Read-only checks via the read model; returns the human-readable preview.

    Raises ValueError to refuse prepare (engine wraps as INVALID_INPUT).
    Reading via the read model means tenant scoping + canonical balance math
    are inherited — no duplicated SQL here.
    """
    invoice_id = inputs["invoice_id"]
    amount: Decimal = inputs["amount"]
    method: str = inputs["payment_method"]

    cq = compile_query(
        ReadQuery(
            "invoices",
            filters=[Filter("id", "=", str(invoice_id))],
            select=["invoice_number", "total_amount", "balance", "is_cancelled"],
        ),
        business_id=user.business_id,
    )
    result = execute_query(cq, session)
    if not result.rows:
        raise ValueError(f"invoice {invoice_id} not found for this business")
    row = result.rows[0]
    if row.get("is_cancelled"):
        raise ValueError(f"invoice {row.get('invoice_number')} is cancelled — payments are refused")

    current_balance = Decimal(row["balance"])
    new_balance = current_balance - amount
    return (
        f"Record {_fmt(amount)} via {method} for invoice {row['invoice_number']} "
        f"(current balance {_fmt(current_balance)}; new balance {_fmt(new_balance)})."
    )


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    """Adapt the engine's typed input dict to PaymentCreate and call the service.

    payment_date defaults to today() when the human leaves it blank — this is the
    only adapter-side default; everything else is enforced by the input specs."""
    payment = service_create_payment(
        session=session,
        current_user=user,
        data=PaymentCreate(
            invoice_id=inputs["invoice_id"],
            amount=inputs["amount"],
            payment_method=PaymentMethod(inputs["payment_method"]),
            payment_date=inputs.get("payment_date") or date.today(),
            reference=inputs.get("reference"),
        ),
    )
    return {
        "payment_id": str(payment.id),
        "invoice_id": str(payment.invoice_id),
        "amount": str(payment.amount),
        "payment_method": payment.payment_method.value,
        "payment_date": payment.payment_date.isoformat(),
    }


DECLARATION = CapabilityDeclaration(
    name="record_payment",
    description="Record a payment against an invoice.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
