from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.invoice import InvoiceRead, InvoiceReadWithItems
from app.models.user import User
from app.services.invoice_service import (
    InvoiceCreateWithItems,
    InvoiceUpdateWithItems,
    create_invoice as service_create_invoice,
    get_invoice as service_get_invoice,
    list_invoices as service_list_invoices,
    update_invoice as service_update_invoice,
)

router = APIRouter()


@router.post("/invoices", response_model=InvoiceRead)
def create_invoice(
    payload: InvoiceCreateWithItems,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InvoiceRead:
    invoice = service_create_invoice(
        session=session,
        current_user=current_user,
        data=payload,
    )
    return invoice


@router.get("/invoices", response_model=List[InvoiceReadWithItems | InvoiceRead])
def list_invoices(
    lead_id: UUID | None = None,
    include_items: bool = False,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[InvoiceReadWithItems | InvoiceRead]:
    invoices = service_list_invoices(
        session=session,
        current_user=current_user,
        lead_id=lead_id,
        include_items=include_items,
    )
    if include_items:
        return [InvoiceReadWithItems.model_validate(invoice) for invoice in invoices]

    return [InvoiceRead.model_validate(invoice) for invoice in invoices]


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


@router.patch("/invoices/{invoice_id}", response_model=InvoiceRead)
def update_invoice(
    invoice_id: UUID,
    payload: InvoiceUpdateWithItems,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InvoiceRead:
    invoice = service_update_invoice(
        session=session,
        current_user=current_user,
        invoice_id=invoice_id,
        data=payload,
    )
    return invoice
