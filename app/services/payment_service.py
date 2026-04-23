from decimal import Decimal
from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.enums import InvoiceStatus, LeadActivityType
from app.models.payment import Payment, PaymentCreate
from app.models.user import User
from app.models.lead import LeadActivity
from app.repositories.invoice_repository import get_invoice_by_id
from app.repositories.lead_repository import create_lead_activity
from app.services.invoice_service import clear_invoice_pdf
from app.services.invoice_service import list_customer_invoices as service_list_customer_invoices
from app.services.invoice_status_service import recompute_invoice_status


def create_payment(
    session: Session,
    current_user: User,
    data: PaymentCreate,
) -> Payment:
    invoice = get_invoice_by_id(
        session=session,
        business_id=current_user.business_id,
        invoice_id=data.invoice_id,
    )
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invoice not found for current business",
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
        create_lead_activity(session, LeadActivity(
            lead_id=invoice.lead_id,
            type=LeadActivityType.PAYMENT_RECORDED,
            description=f"Payment of ₹{payment.amount:,.2f} recorded for {invoice.invoice_number}",
            created_by=current_user.id,
        ))

    return payment


def list_payments(
    session: Session,
    current_user: User,
    invoice_id: UUID | None = None,
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

    statement = statement.order_by(Payment.created_at.desc())
    return list(session.exec(statement).all())


def list_customer_payments(
    session: Session,
    current_user: User,
    customer_id: UUID,
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
    return list(session.exec(statement).all())


