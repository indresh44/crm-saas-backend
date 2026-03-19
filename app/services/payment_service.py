from datetime import date
from decimal import Decimal
from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.enums import InvoiceStatus
from app.models.invoice import Invoice
from app.models.payment import Payment, PaymentCreate
from app.models.user import User
from app.repositories.invoice_repository import get_invoice_by_id


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

    payment_data = data.model_dump()
    payment_data["business_id"] = current_user.business_id
    payment = Payment(**payment_data)

    session.add(payment)
    session.flush()

    _recalculate_invoice_status(session, invoice)
    session.commit()
    session.refresh(payment)

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


def _recalculate_invoice_status(session: Session, invoice: Invoice) -> None:
    statement = select(Payment).where(Payment.invoice_id == invoice.id)
    payments = list(session.exec(statement).all())
    total_paid: Decimal = sum((p.amount for p in payments), Decimal("0"))

    if total_paid >= invoice.total_amount:
        invoice.status = InvoiceStatus.PAID
    elif total_paid > 0:
        invoice.status = InvoiceStatus.PARTIAL
    else:
        invoice.status = InvoiceStatus.SENT

    if invoice.status != InvoiceStatus.PAID and invoice.due_date < date.today():
        invoice.status = InvoiceStatus.OVERDUE

    session.add(invoice)
