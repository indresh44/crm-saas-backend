from datetime import date
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import SQLModel, Session

from app.models.enums import InvoiceStatus
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem, InvoiceItemCreate
from app.models.user import User
from app.repositories.booking_repository import get_booking_by_id
from app.repositories.invoice_repository import (
    create_invoice as repo_create_invoice,
    get_invoice_by_id,
    list_invoices_for_business,
    list_invoices_for_lead,
)
from app.repositories.lead_repository import get_lead_by_id


def _build_invoice_items(
    invoice_id: UUID,
    items: List[InvoiceItemCreate],
) -> List[InvoiceItem]:
    return [item.build_model(invoice_id=invoice_id) for item in items]


def create_invoice(
    session: Session,
    current_user: User,
    data: "InvoiceCreateWithItems",
) -> Invoice:
    booking_id = data.invoice.booking_id
    lead_id = data.invoice.lead_id

    if booking_id is None and lead_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of booking_id or lead_id is required",
        )

    if booking_id is not None:
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

    if lead_id is not None:
        lead = get_lead_by_id(
            session=session,
            business_id=current_user.business_id,
            lead_id=lead_id,
        )
        if lead is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Lead not found for current business",
            )

    invoice_data = data.invoice.model_dump()
    invoice_data["business_id"] = current_user.business_id

    invoice = Invoice(**invoice_data)
    invoice = repo_create_invoice(session, invoice)

    if data.items:
        items = _build_invoice_items(invoice_id=invoice.id, items=data.items)
        session.add_all(items)
        session.commit()

    return invoice


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


def list_invoices(
    session: Session,
    current_user: User,
    lead_id: UUID | None = None,
) -> List[Invoice]:
    if lead_id is not None:
        lead = get_lead_by_id(
            session=session,
            business_id=current_user.business_id,
            lead_id=lead_id,
        )
        if lead is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lead not found",
            )
        return list_invoices_for_lead(
            session=session,
            business_id=current_user.business_id,
            lead_id=lead_id,
        )

    return list_invoices_for_business(session, business_id=current_user.business_id)


class InvoiceData(SQLModel):
    booking_id: Optional[UUID] = None
    lead_id: Optional[UUID] = None
    invoice_number: str
    total_amount: Decimal
    status: InvoiceStatus = InvoiceStatus.DRAFT
    issued_date: date
    due_date: date


class InvoiceCreateWithItems(SQLModel):
    invoice: InvoiceData
    items: Optional[List[InvoiceItemCreate]] = None
