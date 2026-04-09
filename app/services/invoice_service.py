from datetime import date
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import SQLModel, Session

from app.models.enums import InvoiceStatus, LeadActivityType
from app.models.invoice import Invoice, InvoiceListItem
from app.models.invoice_item import InvoiceItem, InvoiceItemCreate
from app.models.user import User
from app.repositories.business_repository import get_business_by_id, increment_invoice_sequence
from app.repositories.customer_repository import get_customer_by_id
from app.repositories.booking_repository import get_booking_by_id
from app.repositories.invoice_repository import (
    get_invoice_by_id,
    get_invoice_with_items,
    list_invoices_enriched,
    list_items_for_invoice,
    replace_invoice_items,
    update_invoice as repo_update_invoice,
)
from app.models.lead import LeadActivity
from app.repositories.lead_repository import create_lead_activity, get_lead_by_id
from app.repositories.payment_repository import list_payments_for_invoice
from app.services.line_item_calculator import calculate_totals
from app.services.invoice_pdf_service import generate_invoice_pdf


def clear_invoice_pdf(session: Session, invoice: Invoice) -> None:
    """Clear cached PDF so it regenerates on next request."""
    if invoice.pdf_url or invoice.pdf_generated_at:
        invoice.pdf_url = None
        invoice.pdf_generated_at = None
        session.add(invoice)
        session.commit()
        session.refresh(invoice)


def _build_invoice_items(
    invoice_id: UUID,
    items: List[dict],
) -> List[InvoiceItem]:
    return [
        InvoiceItem(
            invoice_id=invoice_id,
            catalog_item_id=item.get("catalog_item_id"),
            name=item.get("name", ""),
            description=item["description"],
            unit=item.get("unit", "piece"),
            quantity=item["quantity"],
            unit_price=item["unit_price"],
            gst_percent=item["gst_percent"],
            amount=item["amount"],
            sac_code=item.get("sac_code"),
        )
        for item in items
    ]


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
    business = get_business_by_id(session, current_user.business_id)
    if business is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business not found",
        )

    seq = increment_invoice_sequence(session, current_user.business_id)
    prefix = (business.invoice_prefix or "INV").strip() or "INV"
    invoice_data["invoice_number"] = f"{prefix}-{seq:03d}"
    totals = calculate_totals(data.items or [])
    invoice_data["subtotal"] = totals["subtotal"]
    invoice_data["tax_total"] = totals["tax_total"]
    invoice_data["total_amount"] = totals["total_amount"]

    invoice = Invoice(**invoice_data)
    session.add(invoice)
    session.flush()

    if data.items is not None:
        items = _build_invoice_items(invoice_id=invoice.id, items=totals["items_with_totals"])
        session.add_all(items)

    session.commit()
    session.refresh(invoice)

    if invoice.lead_id is not None:
        create_lead_activity(session, LeadActivity(
            lead_id=invoice.lead_id,
            type=LeadActivityType.INVOICE_CREATED,
            description=f"Invoice {invoice.invoice_number} created (₹{invoice.total_amount:,.2f})",
            created_by=current_user.id,
        ))

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


def update_invoice(
    session: Session,
    current_user: User,
    invoice_id: UUID,
    data: "InvoiceUpdateWithItems",
) -> Invoice:
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

    if invoice.status == InvoiceStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Paid invoices cannot be edited",
        )

    update_data = data.invoice.model_dump(exclude_unset=True)
    requested_status = update_data.get("status")

    if data.items is not None and invoice.status != InvoiceStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only draft invoices can edit line items",
        )

    if requested_status is not None:
        allowed_transitions = {
            InvoiceStatus.DRAFT: {InvoiceStatus.SENT, InvoiceStatus.APPROVED},
            InvoiceStatus.SENT: {InvoiceStatus.APPROVED},
            InvoiceStatus.PARTIAL: set(),
            InvoiceStatus.APPROVED: set(),
        }
        if requested_status not in allowed_transitions.get(invoice.status, set()):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot change status from {invoice.status.value} to {requested_status.value}",
            )

    if data.items is not None:
        totals = calculate_totals(data.items)
        update_data["subtotal"] = totals["subtotal"]
        update_data["tax_total"] = totals["tax_total"]
        update_data["total_amount"] = totals["total_amount"]

    for field, value in update_data.items():
        setattr(invoice, field, value)

    invoice = repo_update_invoice(session, invoice)
    if update_data:
        clear_invoice_pdf(session, invoice)

    if data.items is not None:
        items = _build_invoice_items(
            invoice_id=invoice.id,
            items=totals["items_with_totals"],
        )
        replace_invoice_items(session, invoice_id=invoice.id, items=items)
        session.refresh(invoice)
        clear_invoice_pdf(session, invoice)

    if requested_status == InvoiceStatus.APPROVED and invoice.lead_id is not None:
        create_lead_activity(session, LeadActivity(
            lead_id=invoice.lead_id,
            type=LeadActivityType.INVOICE_APPROVED,
            description=f"Invoice {invoice.invoice_number} approved",
            created_by=current_user.id,
        ))

    return invoice


def list_invoices(
    session: Session,
    current_user: User,
    customer_id: UUID | None = None,
    status: InvoiceStatus | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    lead_id: UUID | None = None,
    limit: int = 20,
    offset: int = 0,
    exclude_draft: bool = False,
) -> tuple[list[InvoiceListItem], int, dict[str, Decimal | int]]:
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

    return list_invoices_enriched(
        session=session,
        business_id=current_user.business_id,
        customer_id=customer_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
        lead_id=lead_id,
        exclude_draft=exclude_draft,
        limit=limit,
        offset=offset,
    )


def list_invoice_items(
    session: Session,
    current_user: User,
    invoice_id: UUID,
) -> List[InvoiceItem]:
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

    return list_items_for_invoice(session=session, invoice_id=invoice_id)


def list_customer_invoices(
    session: Session,
    current_user: User,
    customer_id: UUID,
    status: InvoiceStatus | None = None,
    limit: int = 20,
    offset: int = 0,
    exclude_draft: bool = False,
) -> tuple[list[InvoiceListItem], int, dict[str, Decimal | int]]:
    return list_invoices(
        session=session,
        current_user=current_user,
        customer_id=customer_id,
        status=status,
        limit=limit,
        offset=offset,
        exclude_draft=exclude_draft,
    )


def get_or_generate_pdf(
    session: Session,
    current_user: User,
    invoice_id: UUID,
    force: bool = False,
) -> str:
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
        return invoice.pdf_url

    business = get_business_by_id(session, current_user.business_id)
    if business is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business not found",
        )

    customer = None
    if invoice.lead_id is not None:
        lead = get_lead_by_id(session, current_user.business_id, invoice.lead_id)
        if lead is not None and lead.customer_id is not None:
            customer = get_customer_by_id(session, current_user.business_id, lead.customer_id)

    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot generate PDF: customer not found for this invoice",
        )

    payments = list_payments_for_invoice(session, current_user.business_id, invoice.id)
    payments_total = sum((payment.amount for payment in payments), Decimal("0"))
    return generate_invoice_pdf(
        session=session,
        invoice=invoice,
        business=business,
        customer=customer,
        items=invoice.items,
        payments_total=payments_total,
    )


def generate_pdf_for_public(
    session: Session,
    invoice_id: UUID,
) -> str | None:
    """
    Generate PDF without auth context — for public endpoint.
    Returns pdf_url or None if generation fails.
    """
    from app.repositories.invoice_repository import get_invoice_public

    result = get_invoice_public(session, invoice_id)
    if result is None:
        return None

    invoice, _customer_name, _business_name = result

    if invoice.pdf_url:
        return invoice.pdf_url

    business = get_business_by_id(session, invoice.business_id)
    if business is None:
        return None

    customer = None
    if invoice.lead_id is not None:
        lead = get_lead_by_id(session, invoice.business_id, invoice.lead_id)
        if lead is not None and lead.customer_id is not None:
            customer = get_customer_by_id(session, invoice.business_id, lead.customer_id)

    if customer is None:
        return None

    payments = list_payments_for_invoice(session, invoice.business_id, invoice.id)
    payments_total = sum((payment.amount for payment in payments), Decimal("0"))

    try:
        return generate_invoice_pdf(
            session=session,
            invoice=invoice,
            business=business,
            customer=customer,
            items=invoice.items,
            payments_total=payments_total,
        )
    except Exception:
        return None


class InvoiceData(SQLModel):
    booking_id: Optional[UUID] = None
    lead_id: Optional[UUID] = None
    status: InvoiceStatus = InvoiceStatus.DRAFT
    issued_date: date
    due_date: date


class InvoiceUpdateData(SQLModel):
    issued_date: Optional[date] = None
    due_date: Optional[date] = None
    status: Optional[InvoiceStatus] = None


class InvoiceCreateWithItems(SQLModel):
    invoice: InvoiceData
    items: Optional[List[InvoiceItemCreate]] = None


class InvoiceUpdateWithItems(SQLModel):
    invoice: InvoiceUpdateData
    items: Optional[List[InvoiceItemCreate]] = None
