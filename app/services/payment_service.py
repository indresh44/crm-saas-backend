from datetime import datetime, timezone
from decimal import Decimal
from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.enums import InvoiceStatus, LeadActivityType
from app.models.invoice import Invoice
from app.models.lead import Lead, LeadActivity
from app.models.payment import (
    Payment,
    PaymentAmountUpdate,
    PaymentCreate,
    PaymentMetadataUpdate,
)
from app.models.user import User
from app.repositories.invoice_adjustment_repository import sum_adjustments_for_invoice
from app.repositories.invoice_repository import get_invoice_by_id
from app.repositories.lead_repository import create_lead_activity
from app.repositories.payment_repository import (
    get_payment_by_id,
    list_payments_for_invoice,
)
from app.services.invoice_service import clear_invoice_pdf
from app.services.invoice_service import list_customer_invoices as service_list_customer_invoices
from app.services.invoice_status_service import recompute_invoice_status


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_invoice_for_payment(
    session: Session,
    business_id: UUID,
    invoice_id: UUID,
) -> Invoice:
    invoice = get_invoice_by_id(
        session=session,
        business_id=business_id,
        invoice_id=invoice_id,
    )
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invoice not found for current business",
        )
    return invoice


def _remaining_capacity(
    session: Session,
    invoice: Invoice,
    *,
    excluding_payment_id: UUID | None = None,
) -> Decimal:
    """How much can still be paid on this invoice without overshooting?

    effective = total - adjustments - sum(active payments excluding the
    given one). Used to validate amount-edits and moves.
    """
    total = Decimal(str(invoice.total_amount or 0))
    adjustments = sum_adjustments_for_invoice(session, invoice.id)
    active_payments = list_payments_for_invoice(
        session, invoice.business_id, invoice.id
    )
    paid = sum(
        (Decimal(str(p.amount)) for p in active_payments if p.id != excluding_payment_id),
        Decimal("0"),
    )
    return total - adjustments - paid


def _log_lead_activity(
    session: Session,
    *,
    lead_id: UUID,
    activity_type: LeadActivityType,
    description: str,
    user_id: UUID,
) -> None:
    create_lead_activity(
        session,
        LeadActivity(
            lead_id=lead_id,
            type=activity_type,
            description=description,
            created_by=user_id,
        ),
    )


def create_payment(
    session: Session,
    current_user: User,
    data: PaymentCreate,
) -> Payment:
    invoice = _ensure_invoice_for_payment(
        session, current_user.business_id, data.invoice_id
    )

    if invoice.status == InvoiceStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot record payment on a cancelled invoice.",
        )

    payment_data = data.model_dump()
    payment_data["business_id"] = current_user.business_id
    payment = Payment(**payment_data)

    session.add(payment)
    session.flush()

    clear_invoice_pdf(session, invoice)
    session.commit()
    session.refresh(payment)

    # Recompute status — `paid` fires when effective balance reaches 0,
    # factoring in adjustments (discount / write-off) as well as payments.
    recompute_invoice_status(session, invoice)

    if invoice.lead_id is not None:
        _log_lead_activity(
            session,
            lead_id=invoice.lead_id,
            activity_type=LeadActivityType.PAYMENT_RECORDED,
            description=f"Payment of ₹{payment.amount:,.2f} recorded for {invoice.invoice_number}",
            user_id=current_user.id,
        )

    return payment


def update_payment_metadata(
    session: Session,
    current_user: User,
    payment_id: UUID,
    data: PaymentMetadataUpdate,
) -> Payment:
    """In-place edit of date / method / reference. No money math change."""
    payment = get_payment_by_id(session, current_user.business_id, payment_id)
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        )
    if payment.voided_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot edit a voided payment.",
        )

    invoice = _ensure_invoice_for_payment(
        session, current_user.business_id, payment.invoice_id
    )
    if invoice.status == InvoiceStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot edit a payment on a cancelled invoice.",
        )

    changes: list[str] = []
    if data.payment_date is not None and data.payment_date != payment.payment_date:
        changes.append(f"date {payment.payment_date.isoformat()} → {data.payment_date.isoformat()}")
        payment.payment_date = data.payment_date
    if data.payment_method is not None and data.payment_method != payment.payment_method:
        changes.append(f"method {payment.payment_method.value} → {data.payment_method.value}")
        payment.payment_method = data.payment_method
    if data.reference is not None and (data.reference or None) != payment.reference:
        new_ref = data.reference.strip() if isinstance(data.reference, str) else data.reference
        new_ref = new_ref or None
        changes.append(f"reference '{payment.reference or ''}' → '{new_ref or ''}'")
        payment.reference = new_ref

    if not changes:
        return payment

    payment.edited_at = _utcnow()
    session.add(payment)
    session.commit()
    session.refresh(payment)

    if invoice.lead_id is not None:
        _log_lead_activity(
            session,
            lead_id=invoice.lead_id,
            activity_type=LeadActivityType.PAYMENT_EDITED,
            description=(
                f"Payment of ₹{payment.amount:,.2f} on {invoice.invoice_number} edited "
                f"({'; '.join(changes)})"
            ),
            user_id=current_user.id,
        )

    return payment


def update_payment_amount(
    session: Session,
    current_user: User,
    payment_id: UUID,
    data: PaymentAmountUpdate,
) -> Payment:
    """Void the original + create a new payment with the corrected amount.

    Returns the NEW payment. The old one is preserved with voided_at set
    and its id stored on the new payment's replaces_payment_id for lineage.
    """
    old = get_payment_by_id(session, current_user.business_id, payment_id)
    if old is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        )
    if old.voided_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot edit a voided payment.",
        )

    invoice = _ensure_invoice_for_payment(
        session, current_user.business_id, old.invoice_id
    )
    if invoice.status == InvoiceStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot edit a payment on a cancelled invoice.",
        )

    new_amount = Decimal(str(data.amount))
    if new_amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Amount must be greater than 0.",
        )

    capacity = _remaining_capacity(session, invoice, excluding_payment_id=old.id)
    if new_amount > capacity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Amount exceeds remaining balance. "
                f"At most ₹{capacity:,.2f} can be recorded on this invoice."
            ),
        )

    old_amount = Decimal(str(old.amount))

    old.voided_at = _utcnow()
    old.voided_reason = (data.reason or "amount corrected").strip() or "amount corrected"
    old.voided_by = current_user.id
    session.add(old)

    replacement = Payment(
        invoice_id=old.invoice_id,
        business_id=current_user.business_id,
        amount=new_amount,
        payment_method=old.payment_method,
        payment_date=old.payment_date,
        reference=old.reference,
        replaces_payment_id=old.id,
    )
    session.add(replacement)
    session.flush()

    clear_invoice_pdf(session, invoice)
    session.commit()
    session.refresh(replacement)

    recompute_invoice_status(session, invoice)

    if invoice.lead_id is not None:
        _log_lead_activity(
            session,
            lead_id=invoice.lead_id,
            activity_type=LeadActivityType.PAYMENT_EDITED,
            description=(
                f"Payment on {invoice.invoice_number} corrected: "
                f"₹{old_amount:,.2f} → ₹{new_amount:,.2f}"
                + (f" — {data.reason.strip()}" if data.reason and data.reason.strip() else "")
            ),
            user_id=current_user.id,
        )

    return replacement


def move_payment_to_invoice(
    session: Session,
    current_user: User,
    payment_id: UUID,
    target_invoice_id: UUID,
    reason: str | None = None,
) -> Payment:
    """Void on source invoice + create on target invoice. Same customer scope.

    Returns the NEW payment on the target invoice.
    """
    old = get_payment_by_id(session, current_user.business_id, payment_id)
    if old is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        )
    if old.voided_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot move a voided payment.",
        )
    if old.invoice_id == target_invoice_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Source and target invoices are the same.",
        )

    source_invoice = _ensure_invoice_for_payment(
        session, current_user.business_id, old.invoice_id
    )
    target_invoice = _ensure_invoice_for_payment(
        session, current_user.business_id, target_invoice_id
    )

    if source_invoice.status == InvoiceStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot move a payment off a cancelled invoice.",
        )
    if target_invoice.status == InvoiceStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot move a payment onto a cancelled invoice.",
        )

    # Same-customer guard: both invoices must trace back to the same customer
    # via their leads. If either invoice has no lead, we don't have a
    # customer link to enforce — allow it but note it as relaxed scope.
    source_customer_id = _customer_id_for_invoice(session, source_invoice)
    target_customer_id = _customer_id_for_invoice(session, target_invoice)
    if (
        source_customer_id is not None
        and target_customer_id is not None
        and source_customer_id != target_customer_id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot move a payment between invoices for different customers.",
        )

    move_amount = Decimal(str(old.amount))
    capacity = _remaining_capacity(session, target_invoice)
    if move_amount > capacity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Target invoice has only ₹{capacity:,.2f} of remaining balance — "
                f"can't accept a payment of ₹{move_amount:,.2f}."
            ),
        )

    old.voided_at = _utcnow()
    old.voided_reason = (reason or f"moved to {target_invoice.invoice_number}").strip()
    old.voided_by = current_user.id
    session.add(old)

    replacement = Payment(
        invoice_id=target_invoice.id,
        business_id=current_user.business_id,
        amount=move_amount,
        payment_method=old.payment_method,
        payment_date=old.payment_date,
        reference=old.reference,
        replaces_payment_id=old.id,
    )
    session.add(replacement)
    session.flush()

    clear_invoice_pdf(session, source_invoice)
    clear_invoice_pdf(session, target_invoice)
    session.commit()
    session.refresh(replacement)

    recompute_invoice_status(session, source_invoice)
    recompute_invoice_status(session, target_invoice)

    description = (
        f"Payment of ₹{move_amount:,.2f} moved from "
        f"{source_invoice.invoice_number} to {target_invoice.invoice_number}"
    )
    if source_invoice.lead_id is not None:
        _log_lead_activity(
            session,
            lead_id=source_invoice.lead_id,
            activity_type=LeadActivityType.PAYMENT_MOVED,
            description=description,
            user_id=current_user.id,
        )
    if (
        target_invoice.lead_id is not None
        and target_invoice.lead_id != source_invoice.lead_id
    ):
        _log_lead_activity(
            session,
            lead_id=target_invoice.lead_id,
            activity_type=LeadActivityType.PAYMENT_MOVED,
            description=description,
            user_id=current_user.id,
        )

    return replacement


def void_payment(
    session: Session,
    current_user: User,
    payment_id: UUID,
    reason: str | None = None,
) -> Payment:
    """Mark payment as voided. Recomputes parent invoice status."""
    payment = get_payment_by_id(session, current_user.business_id, payment_id)
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        )
    if payment.voided_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment is already voided.",
        )

    invoice = _ensure_invoice_for_payment(
        session, current_user.business_id, payment.invoice_id
    )

    payment.voided_at = _utcnow()
    payment.voided_reason = (reason or "deleted by user").strip() or "deleted by user"
    payment.voided_by = current_user.id
    session.add(payment)

    clear_invoice_pdf(session, invoice)
    session.commit()
    session.refresh(payment)

    # recompute_invoice_status early-returns on cancelled invoices, so
    # this is safe even if the parent is cancelled.
    recompute_invoice_status(session, invoice)

    if invoice.lead_id is not None:
        _log_lead_activity(
            session,
            lead_id=invoice.lead_id,
            activity_type=LeadActivityType.PAYMENT_VOIDED,
            description=(
                f"Payment of ₹{payment.amount:,.2f} on {invoice.invoice_number} voided"
                + (f" — {payment.voided_reason}" if payment.voided_reason else "")
            ),
            user_id=current_user.id,
        )

    return payment


def list_payments(
    session: Session,
    current_user: User,
    invoice_id: UUID | None = None,
    *,
    include_voided: bool = False,
) -> List[Payment]:
    statement = select(Payment).where(Payment.business_id == current_user.business_id)

    if invoice_id is not None:
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
        statement = statement.where(Payment.invoice_id == invoice_id)

    if not include_voided:
        statement = statement.where(Payment.voided_at.is_(None))

    statement = statement.order_by(Payment.created_at.desc())
    return list(session.exec(statement).all())


def list_customer_payments(
    session: Session,
    current_user: User,
    customer_id: UUID,
    *,
    include_voided: bool = False,
) -> List[Payment]:
    invoices, _, _ = service_list_customer_invoices(
        session=session,
        current_user=current_user,
        customer_id=customer_id,
        limit=100,
        offset=0,
    )
    invoice_ids = [invoice.id for invoice in invoices]
    if not invoice_ids:
        return []

    statement = (
        select(Payment)
        .where(
            Payment.business_id == current_user.business_id,
            Payment.invoice_id.in_(invoice_ids),
        )
        .order_by(Payment.payment_date.desc(), Payment.created_at.desc())
    )
    if not include_voided:
        statement = statement.where(Payment.voided_at.is_(None))
    return list(session.exec(statement).all())


def _customer_id_for_invoice(session: Session, invoice: Invoice) -> UUID | None:
    if invoice.lead_id is None:
        return None
    lead = session.get(Lead, invoice.lead_id)
    if lead is None:
        return None
    return lead.customer_id
