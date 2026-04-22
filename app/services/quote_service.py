from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import SQLModel, Session

from app.core.time_utils import today_in
from app.models.enums import InvoiceStatus, LeadActivityType, QuoteStatus
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.lead import Lead, LeadActivity
from app.models.quote import Quote
from app.models.quote_item import QuoteItem, QuoteItemCreate
from app.models.user import User
from app.repositories.business_repository import get_business_by_id, increment_invoice_sequence
from app.repositories.booking_repository import create_booking as repo_create_booking
from app.repositories.invoice_repository import get_invoice_by_quote_id
from app.repositories.lead_repository import get_lead_by_id
from app.repositories.quote_repository import (
    create_quote as repo_create_quote,
    get_quote_by_id,
    get_quote_with_items,
    list_items_for_quote,
    list_quotes_for_business,
    replace_quote_items,
    update_quote as repo_update_quote,
)
from app.services.line_item_calculator import calculate_totals


def _build_quote_items(
    quote_id: UUID,
    items: List[dict],
) -> List[QuoteItem]:
    return [
        QuoteItem(
            quote_id=quote_id,
            catalog_item_id=item.get("catalog_item_id"),
            name=item.get("name", ""),
            description=item["description"],
            unit=item.get("unit", "piece"),
            quantity=item["quantity"],
            unit_price=item["unit_price"],
            gst_percent=item["gst_percent"],
            amount=item["amount"],
        )
        for item in items
    ]


def _build_invoice_items_from_quote(quote: Quote, invoice_id: UUID) -> List[InvoiceItem]:
    return [
        InvoiceItem(
            invoice_id=invoice_id,
            catalog_item_id=item.catalog_item_id,
            name=item.name,
            description=item.description,
            unit=item.unit,
            quantity=item.quantity,
            unit_price=item.unit_price,
            gst_percent=item.gst_percent,
            amount=item.amount,
        )
        for item in quote.items
    ]


def create_quote(
    session: Session,
    current_user: User,
    data: "QuoteCreateWithItems",
) -> Quote:
    lead = get_lead_by_id(
        session=session,
        business_id=current_user.business_id,
        lead_id=data.quote.lead_id,
    )
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lead not found for current business",
        )

    quote_data = data.quote.model_dump()
    quote_data["business_id"] = current_user.business_id
    totals = calculate_totals(data.items or [])
    quote_data["subtotal"] = totals["subtotal"]
    quote_data["tax_total"] = totals["tax_total"]
    quote_data["total_amount"] = totals["total_amount"]
    quote = Quote(**quote_data)
    quote = repo_create_quote(session, quote)

    if data.items is not None:
        items = _build_quote_items(
            quote_id=quote.id,
            items=totals["items_with_totals"],
        )
        replace_quote_items(session, quote_id=quote.id, items=items)

    return quote


def get_quote(
    session: Session,
    current_user: User,
    quote_id: UUID,
) -> Quote:
    quote = get_quote_by_id(
        session=session,
        business_id=current_user.business_id,
        quote_id=quote_id,
    )
    if quote is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quote not found",
        )
    return quote


def list_quote_items(session: Session, quote_id: UUID) -> List[QuoteItem]:
    return list_items_for_quote(session, quote_id)


def list_quotes_for_lead(
    session: Session,
    current_user: User,
    lead_id: UUID,
    limit: int = 3,
) -> List[Quote]:
    _ = get_lead_by_id(
        session=session,
        business_id=current_user.business_id,
        lead_id=lead_id,
    )
    quotes = [
        quote
        for quote in list_quotes_for_business(session, current_user.business_id)
        if quote.lead_id == lead_id
    ]
    quotes.sort(key=lambda quote: quote.created_at, reverse=True)
    return quotes[:limit]


def update_quote(
    session: Session,
    current_user: User,
    quote_id: UUID,
    data: "QuoteUpdateWithItems",
) -> Quote:
    quote = get_quote(session, current_user, quote_id)

    previous_status = quote.status

    update_data = data.quote.model_dump(exclude_unset=True)
    if data.items is not None:
        totals = calculate_totals(data.items)
        update_data["subtotal"] = totals["subtotal"]
        update_data["tax_total"] = totals["tax_total"]
        update_data["total_amount"] = totals["total_amount"]

    for field, value in update_data.items():
        setattr(quote, field, value)

    quote = repo_update_quote(session, quote)

    if data.items is not None:
        items = _build_quote_items(
            quote_id=quote.id,
            items=totals["items_with_totals"],
        )
        replace_quote_items(session, quote_id=quote.id, items=items)

    if previous_status != QuoteStatus.ACCEPTED and quote.status == QuoteStatus.ACCEPTED:
        _ensure_booking_for_quote(session, current_user, quote)

    return quote


def _ensure_booking_for_quote(
    session: Session,
    current_user: User,
    quote: Quote,
) -> None:
    lead: Lead | None = get_lead_by_id(
        session=session,
        business_id=current_user.business_id,
        lead_id=quote.lead_id,
    )
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lead for quote not found in current business",
        )

    booking = repo_create_booking(
        session=session,
        quote=quote,
        lead=lead,
        business_id=current_user.business_id,
    )
    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create booking from accepted quote",
        )


def convert_quote_to_invoice(
    session: Session,
    current_user: User,
    quote_id: UUID,
) -> Invoice:
    quote = get_quote_with_items(
        session=session,
        business_id=current_user.business_id,
        quote_id=quote_id,
    )
    if quote is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quote not found",
        )

    if quote.status == QuoteStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Quote must be sent or accepted before converting to invoice",
        )
    if quote.status == QuoteStatus.REJECTED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot convert a rejected quote to invoice",
        )

    existing_invoice = get_invoice_by_quote_id(
        session=session,
        business_id=current_user.business_id,
        quote_id=quote.id,
    )
    if existing_invoice is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"An invoice already exists for this quote (invoice_number: {existing_invoice.invoice_number})",
        )

    lead = get_lead_by_id(
        session=session,
        business_id=current_user.business_id,
        lead_id=quote.lead_id,
    )
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lead not found for current business",
        )

    business = get_business_by_id(session, current_user.business_id)
    if business is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business not found",
        )
    today = today_in(business.timezone or "Asia/Kolkata")

    seq = increment_invoice_sequence(session, current_user.business_id)
    prefix = (business.invoice_prefix or "INV").strip() or "INV"
    default_due_days = business.default_due_days if business.default_due_days is not None else 15
    invoice = Invoice(
        business_id=current_user.business_id,
        quote_id=quote.id,
        lead_id=quote.lead_id,
        booking_id=None,
        invoice_number=f"{prefix}-{seq:03d}",
        subtotal=quote.subtotal,
        tax_total=quote.tax_total,
        total_amount=quote.total_amount,
        status=InvoiceStatus.DRAFT,
        issued_date=today,
        due_date=today + timedelta(days=default_due_days),
    )
    session.add(invoice)
    session.flush()

    invoice_items = _build_invoice_items_from_quote(quote, invoice.id)
    if invoice_items:
        session.add_all(invoice_items)

    if quote.status == QuoteStatus.SENT:
        quote.status = QuoteStatus.ACCEPTED
        session.add(quote)

    activity = LeadActivity(
        lead_id=lead.id,
        type=LeadActivityType.NOTE,
        description=f"Invoice {invoice.invoice_number} created from Quote {quote.id}",
        created_by=current_user.id,
    )
    session.add(activity)

    session.commit()
    session.refresh(invoice)
    return invoice


class QuoteData(SQLModel):
    lead_id: UUID
    description: Optional[str] = None
    status: QuoteStatus = QuoteStatus.DRAFT
    is_template: bool = False


class QuoteUpdateData(SQLModel):
    lead_id: Optional[UUID] = None
    description: Optional[str] = None
    status: Optional[QuoteStatus] = None
    is_template: Optional[bool] = None


class QuoteCreateWithItems(SQLModel):
    quote: QuoteData
    items: Optional[List[QuoteItemCreate]] = None


class QuoteUpdateWithItems(SQLModel):
    quote: QuoteUpdateData
    items: Optional[List[QuoteItemCreate]] = None
