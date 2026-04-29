from typing import List
from uuid import UUID
from datetime import date

from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.enums import InvoiceStatus
from app.models.invoice import InvoiceListResponse, InvoiceListSummary, InvoiceRead
from app.models.attachment import AttachmentRead
from app.models.invoice_adjustment import InvoiceAdjustmentCreate, InvoiceAdjustmentRead
from app.models.invoice_item import InvoiceItemRead, InvoiceItemUpdate
from app.models.user import User
from app.repositories.business_repository import get_business_by_id
from app.repositories.customer_repository import get_customer_by_id
from app.repositories.invoice_repository import get_invoice_with_items
from app.repositories.lead_repository import get_lead_by_id
from app.repositories.payment_repository import list_payments_for_invoice
from app.services.invoice_adjustment_service import (
    add_adjustment as service_add_adjustment,
    delete_adjustment as service_delete_adjustment,
    list_adjustments as service_list_adjustments,
)
from app.services.invoice_service import (
    InvoiceCreateWithItems,
    InvoiceUpdateWithItems,
    cancel_invoice as service_cancel_invoice,
    create_invoice as service_create_invoice,
    get_invoice as service_get_invoice,
    list_invoice_items as service_list_invoice_items,
    list_invoices as service_list_invoices,
    update_invoice as service_update_invoice,
    update_invoice_item as service_update_invoice_item,
)
from pydantic import BaseModel
from app.services.attachment_service import list_attachments as service_list_attachments
from app.models.enums import AttachmentEntityType
from app.services.invoice_pdf_service import generate_invoice_pdf

router = APIRouter()


class CancelInvoicePayload(BaseModel):
    reason: str | None = None


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


@router.get("/invoices", response_model=InvoiceListResponse)
def list_invoices(
    customer_id: UUID | None = None,
    status: InvoiceStatus | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    lead_id: UUID | None = None,
    invoice_number: str | None = Query(default=None, max_length=100),
    include_cancelled: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InvoiceListResponse:
    invoices, total, summary = service_list_invoices(
        session=session,
        current_user=current_user,
        customer_id=customer_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
        lead_id=lead_id,
        invoice_number=invoice_number,
        include_cancelled=include_cancelled,
        limit=limit,
        offset=offset,
    )

    return InvoiceListResponse(
        items=invoices,
        total=total,
        limit=limit,
        offset=offset,
        summary=InvoiceListSummary(**summary),
    )


@router.get("/invoices/{invoice_id}/items", response_model=List[InvoiceItemRead])
def get_invoice_items(
    invoice_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[InvoiceItemRead]:
    items = service_list_invoice_items(
        session=session,
        current_user=current_user,
        invoice_id=invoice_id,
    )
    return items


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


@router.post("/invoices/{invoice_id}/cancel", response_model=InvoiceRead)
def cancel_invoice(
    invoice_id: UUID,
    payload: CancelInvoicePayload | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InvoiceRead:
    reason = payload.reason if payload is not None else None
    return service_cancel_invoice(
        session=session,
        current_user=current_user,
        invoice_id=invoice_id,
        reason=reason,
    )


@router.patch("/invoices/{invoice_id}/items/{item_id}", response_model=InvoiceItemRead)
def update_invoice_item(
    invoice_id: UUID,
    item_id: UUID,
    payload: InvoiceItemUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InvoiceItemRead:
    """Update an invoice item's deliverables, name, or description."""
    return service_update_invoice_item(
        session=session,
        current_user=current_user,
        invoice_id=invoice_id,
        item_id=item_id,
        payload=payload,
    )


@router.post(
    "/invoices/{invoice_id}/adjustments",
    response_model=InvoiceAdjustmentRead,
    status_code=201,
)
def add_invoice_adjustment(
    invoice_id: UUID,
    payload: InvoiceAdjustmentCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InvoiceAdjustmentRead:
    return service_add_adjustment(
        session=session,
        current_user=current_user,
        invoice_id=invoice_id,
        data=payload,
    )


@router.get(
    "/invoices/{invoice_id}/adjustments",
    response_model=List[InvoiceAdjustmentRead],
)
def list_invoice_adjustments(
    invoice_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[InvoiceAdjustmentRead]:
    return service_list_adjustments(
        session=session,
        current_user=current_user,
        invoice_id=invoice_id,
    )


@router.delete(
    "/invoices/{invoice_id}/adjustments/{adjustment_id}",
    status_code=204,
)
def delete_invoice_adjustment(
    invoice_id: UUID,
    adjustment_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    service_delete_adjustment(
        session=session,
        current_user=current_user,
        invoice_id=invoice_id,
        adjustment_id=adjustment_id,
    )


@router.get(
    "/invoices/{invoice_id}/items/{item_id}/attachments",
    response_model=List[AttachmentRead],
)
def get_invoice_item_attachments(
    invoice_id: UUID,
    item_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[AttachmentRead]:
    """Get attachments for a specific invoice item."""
    # Verify invoice belongs to user's business
    invoice = service_get_invoice(
        session=session,
        current_user=current_user,
        invoice_id=invoice_id,
    )
    return service_list_attachments(
        session=session,
        current_user=current_user,
        entity_type=AttachmentEntityType.INVOICE_ITEM,
        entity_id=item_id,
    )


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


@router.get("/invoices/{invoice_id}/pdf/download")
async def download_invoice_pdf(
    invoice_id: UUID,
    force: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Stream the actual PDF bytes (avoids CORS issues with R2)."""
    invoice = get_invoice_with_items(
        session=session,
        business_id=current_user.business_id,
        invoice_id=invoice_id,
    )
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    pdf_url = invoice.pdf_url
    if not pdf_url or force:
        business = get_business_by_id(session, current_user.business_id)
        if business is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found")

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

    async with httpx.AsyncClient() as client:
        r2_response = await client.get(pdf_url)

    if r2_response.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch PDF from storage")

    filename = f"{invoice.invoice_number}.pdf"
    return StreamingResponse(
        iter([r2_response.content]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, max-age=3600",
        },
    )
