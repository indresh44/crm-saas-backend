from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.invoice import InvoiceCreate, InvoiceRead
from app.models.user import User
from app.services.invoice_service import (
    create_invoice_for_booking as service_create_invoice_for_booking,
    get_invoice as service_get_invoice,
    list_invoices as service_list_invoices,
)

router = APIRouter()


@router.post("/invoices", response_model=InvoiceRead)
def create_invoice(
    booking_id: UUID,
    payload: InvoiceCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InvoiceRead:
    invoice = service_create_invoice_for_booking(
        session=session,
        current_user=current_user,
        booking_id=booking_id,
        data=payload,
    )
    return invoice


@router.get("/invoices", response_model=List[InvoiceRead])
def list_invoices(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[InvoiceRead]:
    invoices = service_list_invoices(session=session, current_user=current_user)
    return invoices


@router.get("/invoices/{invoice_id}", response_model=InvoiceRead)
def get_invoice(
    invoice_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InvoiceRead:
    invoice = service_get_invoice(
        session=session,
        current_user=current_user,
        invoice_id=invoice_id,
    )
    return invoice

