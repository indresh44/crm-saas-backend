"""Business logic for invoice adjustments (discount / write-off)."""

from __future__ import annotations

from decimal import Decimal
from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.enums import InvoiceStatus
from app.models.invoice_adjustment import (
    InvoiceAdjustment,
    InvoiceAdjustmentCreate,
)
from app.models.user import User
from app.repositories.invoice_adjustment_repository import (
    create_adjustment as repo_create_adjustment,
    delete_adjustment as repo_delete_adjustment,
    get_adjustment_by_id,
    list_adjustments_for_invoice,
    sum_adjustments_for_invoice,
)
from app.repositories.invoice_repository import get_invoice_by_id
from app.repositories.payment_repository import list_payments_for_invoice
from app.services.invoice_service import clear_invoice_pdf
from app.services.invoice_status_service import recompute_invoice_status


# Statuses where the user can add/remove adjustments.
_MUTABLE_STATUSES = {
    InvoiceStatus.SENT,
    InvoiceStatus.APPROVED,
    InvoiceStatus.PARTIAL,
}


def add_adjustment(
    session: Session,
    current_user: User,
    invoice_id: UUID,
    data: InvoiceAdjustmentCreate,
) -> InvoiceAdjustment:
    invoice = get_invoice_by_id(
        session=session,
        business_id=current_user.business_id,
        invoice_id=invoice_id,
    )
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    if invoice.status == InvoiceStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot add adjustments to a cancelled invoice.",
        )
    if invoice.status == InvoiceStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Adjustments aren't allowed on draft invoices — edit the invoice "
                "directly instead."
            ),
        )
    if invoice.status == InvoiceStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This invoice is already paid; adjustments can't be added.",
        )
    if invoice.status not in _MUTABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Adjustments not allowed on status '{invoice.status.value}'.",
        )

    remaining_balance = _remaining_balance(session, current_user.business_id, invoice_id)
    if data.amount > remaining_balance:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Adjustment amount ({data.amount}) exceeds the remaining balance "
                f"({remaining_balance})."
            ),
        )

    record = InvoiceAdjustment(
        invoice_id=invoice_id,
        amount=data.amount,
        adjustment_type=data.adjustment_type.value,
        reason=(data.reason or "").strip() or None,
        created_by=current_user.id,
    )
    record = repo_create_adjustment(session, record)

    clear_invoice_pdf(session, invoice)
    recompute_invoice_status(session, invoice)

    return record


def list_adjustments(
    session: Session,
    current_user: User,
    invoice_id: UUID,
) -> List[InvoiceAdjustment]:
    invoice = get_invoice_by_id(
        session=session,
        business_id=current_user.business_id,
        invoice_id=invoice_id,
    )
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )
    return list_adjustments_for_invoice(session, invoice_id)


def delete_adjustment(
    session: Session,
    current_user: User,
    invoice_id: UUID,
    adjustment_id: UUID,
) -> None:
    invoice = get_invoice_by_id(
        session=session,
        business_id=current_user.business_id,
        invoice_id=invoice_id,
    )
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    adjustment = get_adjustment_by_id(session, adjustment_id)
    if adjustment is None or adjustment.invoice_id != invoice_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Adjustment not found",
        )

    repo_delete_adjustment(session, adjustment)

    clear_invoice_pdf(session, invoice)
    recompute_invoice_status(session, invoice)


def _remaining_balance(
    session: Session,
    business_id: UUID,
    invoice_id: UUID,
) -> Decimal:
    """Live balance = total − existing adjustments − existing payments."""
    invoice = get_invoice_by_id(
        session=session,
        business_id=business_id,
        invoice_id=invoice_id,
    )
    total = Decimal(str(invoice.total_amount or 0)) if invoice else Decimal("0")
    adjustments = sum_adjustments_for_invoice(session, invoice_id)
    payments = sum(
        (Decimal(str(p.amount)) for p in list_payments_for_invoice(session, business_id, invoice_id)),
        Decimal("0"),
    )
    return total - adjustments - payments
