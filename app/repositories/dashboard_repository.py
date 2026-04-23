from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, true
from sqlmodel import Session, select

from app.models.customer import Customer
from app.models.enums import InvoiceStatus
from app.models.invoice import Invoice
from app.models.lead import Lead
from app.models.invoice_adjustment import InvoiceAdjustment
from app.models.payment import Payment


def fetch_monthly_collections(
    session: Session,
    business_id: UUID,
) -> tuple[Decimal, Decimal]:
    today = date.today()
    this_month_start = today.replace(day=1)
    if this_month_start.month == 12:
        next_month_start = date(this_month_start.year + 1, 1, 1)
    else:
        next_month_start = date(this_month_start.year, this_month_start.month + 1, 1)

    if this_month_start.month == 1:
        last_month_start = date(this_month_start.year - 1, 12, 1)
    else:
        last_month_start = date(this_month_start.year, this_month_start.month - 1, 1)

    this_month_sum = func.coalesce(
        func.sum(
            case(
                (
                    and_(
                        Payment.payment_date >= this_month_start,
                        Payment.payment_date < next_month_start,
                    ),
                    Payment.amount,
                ),
                else_=0,
            )
        ),
        0,
    )
    last_month_sum = func.coalesce(
        func.sum(
            case(
                (
                    and_(
                        Payment.payment_date >= last_month_start,
                        Payment.payment_date < this_month_start,
                    ),
                    Payment.amount,
                ),
                else_=0,
            )
        ),
        0,
    )

    statement = select(this_month_sum, last_month_sum).where(Payment.business_id == business_id)
    return session.exec(statement).one()


def fetch_outstanding_and_overdue(
    session: Session,
    business_id: UUID,
    today: date,
) -> dict[str, Any]:
    payment_totals_sq = (
        select(
            Payment.invoice_id.label("invoice_id"),
            func.coalesce(func.sum(Payment.amount), 0).label("amount_paid"),
        )
        .where(Payment.business_id == business_id)
        .group_by(Payment.invoice_id)
        .subquery()
    )

    adjustment_totals_sq = (
        select(
            InvoiceAdjustment.invoice_id.label("invoice_id"),
            func.coalesce(func.sum(InvoiceAdjustment.amount), 0).label("adjustments_total"),
        )
        .group_by(InvoiceAdjustment.invoice_id)
        .subquery()
    )

    balance_due_expr = (
        Invoice.total_amount
        - func.coalesce(payment_totals_sq.c.amount_paid, 0)
        - func.coalesce(adjustment_totals_sq.c.adjustments_total, 0)
    )
    outstanding_filter = and_(
        Invoice.business_id == business_id,
        Invoice.status.notin_(
            [InvoiceStatus.PAID, InvoiceStatus.DRAFT, InvoiceStatus.CANCELLED]
        ),
    )

    summary_sq = (
        select(
            func.coalesce(func.sum(balance_due_expr), 0).label("total_outstanding"),
            func.count(Invoice.id).label("outstanding_invoice_count"),
        )
        .select_from(Invoice)
        .outerjoin(payment_totals_sq, Invoice.id == payment_totals_sq.c.invoice_id)
        .outerjoin(adjustment_totals_sq, Invoice.id == adjustment_totals_sq.c.invoice_id)
        .where(outstanding_filter)
        .subquery()
    )

    overdue_sq = (
        select(
            Invoice.id.label("invoice_id"),
            Invoice.lead_id.label("lead_id"),
            Invoice.invoice_number.label("invoice_number"),
            Customer.name.label("customer_name"),
            Customer.phone.label("customer_phone"),
            Invoice.total_amount.label("total_amount"),
            func.coalesce(payment_totals_sq.c.amount_paid, 0).label("amount_paid"),
            balance_due_expr.label("balance_due"),
            Invoice.due_date.label("due_date"),
        )
        .select_from(Invoice)
        .outerjoin(payment_totals_sq, Invoice.id == payment_totals_sq.c.invoice_id)
        .outerjoin(adjustment_totals_sq, Invoice.id == adjustment_totals_sq.c.invoice_id)
        .outerjoin(Lead, Invoice.lead_id == Lead.id)
        .outerjoin(Customer, Lead.customer_id == Customer.id)
        .where(
            outstanding_filter,
            Invoice.due_date < today,
            balance_due_expr > 0,
        )
        .order_by(Invoice.due_date.asc())
        .limit(5)
        .subquery()
    )

    statement = (
        select(
            summary_sq.c.total_outstanding,
            summary_sq.c.outstanding_invoice_count,
            overdue_sq.c.invoice_id,
            overdue_sq.c.lead_id,
            overdue_sq.c.invoice_number,
            overdue_sq.c.customer_name,
            overdue_sq.c.customer_phone,
            overdue_sq.c.total_amount,
            overdue_sq.c.amount_paid,
            overdue_sq.c.balance_due,
            overdue_sq.c.due_date,
        )
        .select_from(summary_sq)
        .outerjoin(overdue_sq, true())
    )

    rows = session.exec(statement).all()
    if not rows:
        return {
            "total_outstanding": Decimal("0"),
            "outstanding_invoice_count": 0,
            "overdue_invoices": [],
        }

    first_row = rows[0]
    overdue_invoices = []
    for row in rows:
        if row.invoice_id is None:
            continue
        overdue_invoices.append(
            {
                "invoice_id": row.invoice_id,
                "lead_id": row.lead_id,
                "invoice_number": row.invoice_number,
                "customer_name": row.customer_name,
                "customer_phone": row.customer_phone,
                "total_amount": row.total_amount,
                "amount_paid": row.amount_paid,
                "balance_due": row.balance_due,
                "due_date": row.due_date,
            }
        )

    return {
        "total_outstanding": first_row.total_outstanding,
        "outstanding_invoice_count": first_row.outstanding_invoice_count,
        "overdue_invoices": overdue_invoices,
    }
