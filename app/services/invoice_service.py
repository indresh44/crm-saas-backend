from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.invoice import Invoice, InvoiceCreate
from app.models.user import User
from app.repositories.booking_repository import get_booking_by_id
from app.repositories.invoice_repository import (
    create_invoice as repo_create_invoice,
    get_invoice_by_id,
    list_invoices_for_business,
)


def create_invoice_for_booking(
    session: Session,
    current_user: User,
    booking_id: UUID,
    data: InvoiceCreate,
) -> Invoice:
    booking = get_booking_by_id(
        session=session,
        business_id=current_user.business_id,
        booking_id=booking_id,
    )
    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Booking not found for current business",
        )

    invoice_data = data.model_dump()
    invoice_data["business_id"] = current_user.business_id
    invoice_data["booking_id"] = booking.id

    invoice = Invoice(**invoice_data)
    return repo_create_invoice(session, invoice)


def get_invoice(
    session: Session,
    current_user: User,
    invoice_id: UUID,
) -> Invoice:
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
    return invoice


def list_invoices(session: Session, current_user: User) -> List[Invoice]:
    return list_invoices_for_business(session, business_id=current_user.business_id)

