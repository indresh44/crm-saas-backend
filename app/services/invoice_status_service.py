"""
Central invoice status recompute.

Single source of truth for: "given the current state of an invoice's
total + adjustments + payments, what status should it be?"

Called whenever adjustments or payments change, so the status stays
consistent without every caller duplicating the logic.

Rules:
- effective_balance = total_amount − sum(adjustments) − sum(payments)
- balance <= 0   → PAID  (covers full payment, full discount, full write-off, any combination)
- balance > 0 and any payments → PARTIAL
- balance > 0 and no payments  → leave current status alone (APPROVED/SENT/DRAFT are
  user-driven states; money math shouldn't auto-downgrade them)
- DRAFT is never auto-flipped to PAID — draft invoices shouldn't be reachable here anyway
  (no one records payments or adjustments on a draft), but we guard defensively.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlmodel import Session

from app.models.enums import InvoiceStatus
from app.models.invoice import Invoice
from app.repositories.invoice_adjustment_repository import sum_adjustments_for_invoice


def recompute_invoice_status(
    session: Session,
    invoice: Invoice,
) -> Invoice:
    """
    Recompute and persist the invoice status based on current adjustments + payments.
    Returns the (possibly updated) invoice. No-ops on DRAFT.
    """
    if invoice.status == InvoiceStatus.DRAFT:
        return invoice

    total = Decimal(str(invoice.total_amount or 0))
    adjustments_total = sum_adjustments_for_invoice(session, invoice.id)
    payments_total = _sum_payments(session, invoice.business_id, invoice.id)

    balance = total - adjustments_total - payments_total

    new_status: InvoiceStatus | None = None
    if balance <= 0:
        new_status = InvoiceStatus.PAID
    elif payments_total > 0:
        new_status = InvoiceStatus.PARTIAL
    else:
        # Balance positive, no payments. Flip back if we're coming from PAID/PARTIAL
        # (e.g. user deleted an adjustment). Otherwise leave APPROVED/SENT as-is.
        if invoice.status in {InvoiceStatus.PAID, InvoiceStatus.PARTIAL}:
            new_status = InvoiceStatus.APPROVED

    if new_status is not None and new_status != invoice.status:
        invoice.status = new_status
        session.add(invoice)
        session.commit()
        session.refresh(invoice)

    return invoice


def recompute_invoice_status_by_id(
    session: Session,
    invoice_id: UUID,
) -> Invoice | None:
    """Convenience wrapper when the caller only has the invoice_id."""
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        return None
    return recompute_invoice_status(session, invoice)


def _sum_payments(session: Session, business_id: UUID, invoice_id: UUID) -> Decimal:
    """Imported lazily to avoid circular repo dependencies at module load."""
    from app.repositories.payment_repository import list_payments_for_invoice

    payments = list_payments_for_invoice(session, business_id, invoice_id)
    return sum((Decimal(str(p.amount)) for p in payments), Decimal("0"))
