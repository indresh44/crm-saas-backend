"""Public (unauthenticated) invoice endpoints for branded sharing."""

from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.core.database import get_session
from app.models.enums import InvoiceStatus
from app.models.invoice import InvoicePublicMeta
from app.repositories.invoice_repository import get_invoice_public
from app.services.invoice_service import generate_pdf_for_public

router = APIRouter()


@router.get("/invoices/{invoice_id}/meta", response_model=InvoicePublicMeta)
def get_invoice_meta(
    invoice_id: UUID,
    session: Session = Depends(get_session),
) -> InvoicePublicMeta:
    """Return minimal invoice info for OG tags and public page."""
    result = get_invoice_public(session, invoice_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    invoice, customer_name, business_name = result

    if invoice.status == InvoiceStatus.DRAFT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    items_count = len(invoice.items) if invoice.items else 0

    return InvoicePublicMeta(
        invoice_number=invoice.invoice_number,
        total_amount=invoice.total_amount,
        due_date=invoice.due_date,
        status=invoice.status,
        customer_name=customer_name,
        business_name=business_name,
        items_count=items_count,
    )


@router.get("/invoices/{invoice_id}/pdf")
async def download_invoice_pdf_public(
    invoice_id: UUID,
    session: Session = Depends(get_session),
):
    """Stream PDF bytes publicly (no auth). Used by the public invoice page."""
    result = get_invoice_public(session, invoice_id)
    print(f"\n get_invoice_pdf_public: fetched invoice {invoice_id}: {result} \n")
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    invoice, _customer_name, _business_name = result

    pdf_url = invoice.pdf_url
    if not pdf_url:
        pdf_url = generate_pdf_for_public(session, invoice_id)
        if not pdf_url:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="PDF could not be generated",
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
            "Cache-Control": "public, max-age=3600",
        },
    )
