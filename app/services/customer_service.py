from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.core.phone import normalize_phone_value
from app.core.time_utils import today_in
from app.models.booking import Booking
from app.models.customer import Customer, CustomerCreateRequest, CustomerUpdate
from app.models.enums import InvoiceStatus
from app.models.invoice import Invoice
from app.models.lead import Lead
from app.models.payment import Payment
from app.models.user import User
from app.repositories.business_repository import get_business_by_id
from app.repositories.invoice_adjustment_repository import sum_adjustments_for_invoice
from app.repositories.customer_repository import (
    create_customer as repo_create_customer,
    get_customer_by_phone as repo_get_customer_by_phone,
    get_customer_by_id,
    get_customer_summary as repo_get_customer_summary,
    list_customers_for_business,
    search_customers as repo_search_customers,
    update_customer as repo_update_customer,
)


def normalize_phone(phone: str) -> str:
    return normalize_phone_value(phone)


def create_customer(
    session: Session,
    current_user: User,
    data: CustomerCreateRequest,
) -> Customer:
    customer_data = data.model_dump()
    customer_data["business_id"] = current_user.business_id
    customer_data["phone_normalized"] = normalize_phone(data.phone)

    if not customer_data["phone_normalized"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid phone number",
        )

    existing_customer = repo_get_customer_by_phone(
        session=session,
        business_id=current_user.business_id,
        normalized_phone=customer_data["phone_normalized"],
    )
    if existing_customer is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Customer with this phone already exists",
        )

    customer = Customer(**customer_data)
    return repo_create_customer(session, customer)


def get_customer(
    session: Session,
    current_user: User,
    customer_id: UUID,
) -> Customer:
    customer = get_customer_by_id(
        session=session,
        business_id=current_user.business_id,
        customer_id=customer_id,
    )
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )
    return customer


def list_customers(session: Session, current_user: User) -> List[Customer]:
    return list_customers_for_business(session, business_id=current_user.business_id)


def search_customers(
    session: Session,
    current_user: User,
    query: str,
    limit: int = 10,
) -> List[Customer]:
    cleaned_query = query.strip()
    if len(cleaned_query) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query must be at least 2 characters long",
        )

    return repo_search_customers(
        session=session,
        business_id=current_user.business_id,
        query=cleaned_query,
        limit=limit,
    )


def get_customer_by_phone(
    session: Session,
    current_user: User,
    phone: str,
) -> Optional[Customer]:
    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid phone number",
        )

    return repo_get_customer_by_phone(
        session=session,
        business_id=current_user.business_id,
        normalized_phone=normalized_phone,
    )


def get_customer_outstanding(
    session: Session,
    business_id: UUID,
    customer_id: UUID,
) -> dict[str, Decimal | int]:
    customer = get_customer_by_id(
        session=session,
        business_id=business_id,
        customer_id=customer_id,
    )
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )

    direct_invoice_statement = (
        select(Invoice)
        .join(Lead, Invoice.lead_id == Lead.id)
        .where(
            Invoice.business_id == business_id,
            Lead.customer_id == customer_id,
            Invoice.status != InvoiceStatus.CANCELLED,
        )
    )
    booking_invoice_statement = (
        select(Invoice)
        .join(Booking, Invoice.booking_id == Booking.id)
        .join(Lead, Booking.lead_id == Lead.id)
        .where(
            Invoice.business_id == business_id,
            Lead.customer_id == customer_id,
            Invoice.status != InvoiceStatus.CANCELLED,
        )
    )

    invoices = {
        invoice.id: invoice
        for invoice in [
            *session.exec(direct_invoice_statement).all(),
            *session.exec(booking_invoice_statement).all(),
        ]
    }
    invoice_ids = list(invoices.keys())

    total_invoiced = sum((invoice.total_amount for invoice in invoices.values()), Decimal("0"))
    total_paid = Decimal("0")
    total_adjustments = Decimal("0")
    overdue_invoices = 0

    if invoice_ids:
        payment_statement = select(Payment).where(Payment.invoice_id.in_(invoice_ids))
        payments = list(session.exec(payment_statement).all())
        total_paid = sum((payment.amount for payment in payments), Decimal("0"))

        payments_by_invoice_id: dict[UUID, Decimal] = {}
        for payment in payments:
            payments_by_invoice_id[payment.invoice_id] = payments_by_invoice_id.get(payment.invoice_id, Decimal("0")) + payment.amount

        adjustments_by_invoice_id: dict[UUID, Decimal] = {
            invoice_id: sum_adjustments_for_invoice(session, invoice_id)
            for invoice_id in invoice_ids
        }
        total_adjustments = sum(adjustments_by_invoice_id.values(), Decimal("0"))

        business = get_business_by_id(session, business_id)
        tz = (business.timezone if business else None) or "Asia/Kolkata"
        today = today_in(tz)
        overdue_invoices = sum(
            1
            for invoice in invoices.values()
            if (
                invoice.due_date < today
                and (
                    payments_by_invoice_id.get(invoice.id, Decimal("0"))
                    + adjustments_by_invoice_id.get(invoice.id, Decimal("0"))
                )
                < invoice.total_amount
            )
        )

    return {
        "total_invoiced": total_invoiced,
        "total_paid": total_paid,
        "outstanding": total_invoiced - total_paid - total_adjustments,
        "overdue_invoices": overdue_invoices,
    }


def update_customer(
    session: Session,
    current_user: User,
    customer_id: UUID,
    data: CustomerUpdate,
) -> Customer:
    customer = get_customer(session, current_user, customer_id)
    update_data = data.model_dump(exclude_unset=True)

    if "phone" in update_data:
        normalized_phone = normalize_phone(update_data["phone"])
        if not normalized_phone:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid phone number",
            )

        existing_customer = repo_get_customer_by_phone(
            session=session,
            business_id=current_user.business_id,
            normalized_phone=normalized_phone,
        )
        if existing_customer is not None and existing_customer.id != customer.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Customer with this phone already exists",
            )
        customer.phone_normalized = normalized_phone

    for field, value in update_data.items():
        setattr(customer, field, value)
    return repo_update_customer(session, customer)


def get_customer_summary(
    session: Session,
    current_user: User,
    customer_id: UUID,
) -> dict:
    summary = repo_get_customer_summary(
        session=session,
        business_id=current_user.business_id,
        customer_id=customer_id,
    )
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )
    return summary
