from typing import List
from uuid import UUID

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.invoice import InvoiceRead, InvoiceReadWithItems
from app.models.user import User
from app.repositories.business_repository import get_business_by_id
from app.repositories.customer_repository import get_customer_by_id
from app.repositories.invoice_repository import get_invoice_with_items
from app.repositories.lead_repository import get_lead_by_id
from app.repositories.payment_repository import list_payments_for_invoice
from app.services.invoice_service import (
    InvoiceCreateWithItems,
    InvoiceUpdateWithItems,
    create_invoice as service_create_invoice,
    get_invoice as service_get_invoice,
    list_invoices as service_list_invoices,
    update_invoice as service_update_invoice,
)
from app.services.invoice_pdf_service import generate_invoice_pdf

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


@router.get("/invoices/{invoice_id}/pdf")
def get_invoice_pdf(
    invoice_id: UUID,
    force: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    invoice = get_invoice_with_items(
        session=session,
        business_id=current_user.business_id,
        invoice_id=invoice_id,
    )
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    if invoice.pdf_url and not force:
        return {"pdf_url": invoice.pdf_url}

    business = get_business_by_id(session, current_user.business_id)
    if business is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business not found",
        )

    customer = None
    if invoice.lead_id is not None:
        lead = get_lead_by_id(session, current_user.business_id, invoice.lead_id)
        if lead and lead.customer_id:
            customer = get_customer_by_id(session, current_user.business_id, lead.customer_id)

    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot generate PDF: customer not found for this invoice",
        )

    payments = list_payments_for_invoice(session, current_user.business_id, invoice.id)
    payments_total = sum((payment.amount for payment in payments), Decimal("0"))

    pdf_url = generate_invoice_pdf(
        session=session,
        invoice=invoice,
        business=business,
        customer=customer,
        items=invoice.items,
        payments_total=payments_total,
    )
    return {"pdf_url": pdf_url}
