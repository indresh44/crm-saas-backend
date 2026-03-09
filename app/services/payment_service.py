from decimal import Decimal
from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.enums import InvoiceStatus
from app.models.invoice import Invoice
from app.models.payment import Payment, PaymentCreate
from app.models.user import User
from app.repositories.invoice_repository import get_invoice_by_id, update_invoice
from app.repositories.payment_repository import (
    create_payment as repo_create_payment,
    list_payments_for_business,
)


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
    payment = repo_create_payment(session, payment)

    _recalculate_invoice_status(session, invoice)

    return payment


def list_payments(session: Session, current_user: User) -> List[Payment]:
    return list_payments_for_business(session, business_id=current_user.business_id)


def _recalculate_invoice_status(session: Session, invoice: Invoice) -> None:
    statement = select(Payment).where(Payment.invoice_id == invoice.id)
    payments = list(session.exec(statement).all())
    paid_amount: Decimal = sum((p.amount for p in payments), Decimal("0"))

    if paid_amount >= invoice.total_amount:
        invoice.status = InvoiceStatus.PAID
    elif paid_amount > 0:
        invoice.status = InvoiceStatus.PARTIAL
    else:
        invoice.status = InvoiceStatus.SENT

    update_invoice(session, invoice)

