from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlmodel import Session

from app.models.dashboard import OverdueInvoiceSummary, PaymentSummaryRead
from app.repositories.dashboard_repository import (
    fetch_monthly_collections,
    fetch_outstanding_and_overdue,
)


def _to_float(value: Decimal | int | float) -> float:
    return float(value)


def get_payment_summary(
    session: Session,
    business_id: UUID,
) -> PaymentSummaryRead:
    today = date.today()
    collections_this_month, collections_last_month = fetch_monthly_collections(
        session=session,
        business_id=business_id,
    )

    outstanding_data = fetch_outstanding_and_overdue(
        session=session,
        business_id=business_id,
        today=today,
    )

    overdue_invoices = [
        OverdueInvoiceSummary(
            invoice_id=str(item["invoice_id"]),
            lead_id=str(item["lead_id"]) if item["lead_id"] is not None else None,
            invoice_number=item["invoice_number"],
            customer_name=item["customer_name"],
            customer_phone=item["customer_phone"],
            total_amount=_to_float(item["total_amount"]),
            amount_paid=_to_float(item["amount_paid"]),
            balance_due=_to_float(item["balance_due"]),
            due_date=item["due_date"],
            days_overdue=(today - item["due_date"]).days,
        )
        for item in outstanding_data["overdue_invoices"]
    ]

    return PaymentSummaryRead(
        collections_this_month=_to_float(collections_this_month),
        collections_last_month=_to_float(collections_last_month),
        total_outstanding=_to_float(outstanding_data["total_outstanding"]),
        outstanding_invoice_count=int(outstanding_data["outstanding_invoice_count"]),
        overdue_invoices=overdue_invoices,
    )
