from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, List, Optional
from uuid import UUID

from sqlalchemy import and_, case, desc, func, or_
from sqlmodel import Session, select

from app.core.phone import normalize_phone_value
from app.models.customer import Customer
from app.models.enums import InvoiceStatus, MeetingStatus
from app.models.invoice import Invoice
from app.models.lead import Lead, LeadActivity
from app.models.meeting import Meeting
from app.models.payment import Payment
from app.models.pipeline import PipelineStage


def create_customer(session: Session, customer: Customer) -> Customer:
    session.add(customer)
    session.commit()
    session.refresh(customer)
    return customer


def get_customer_by_id(
    session: Session,
    business_id: UUID,
    customer_id: UUID,
) -> Optional[Customer]:
    statement = select(Customer).where(
        Customer.id == customer_id,
        Customer.business_id == business_id,
    )
    return session.exec(statement).first()


def get_customers_by_phone(
    session: Session,
    business_id: UUID,
    phone: str,
) -> List[Customer]:
    """Return customers matching the given phone (for auto-linking conversations)."""
    normalized_phone = normalize_phone_value(phone)
    statement = select(Customer).where(
        Customer.business_id == business_id,
        Customer.phone_normalized == normalized_phone,
    )
    return list(session.exec(statement).all())


def list_customers_for_business(session: Session, business_id: UUID) -> List[Customer]:
    statement = select(Customer).where(Customer.business_id == business_id)
    return list(session.exec(statement).all())


def search_customers(
    session: Session,
    business_id: UUID,
    query: str,
    limit: int = 10,
) -> List[Customer]:
    normalized_query = normalize_phone_value(query)
    filters = [Customer.name.ilike(f"%{query}%")]
    if normalized_query:
        filters.append(Customer.phone_normalized.like(f"%{normalized_query}%"))

    statement = (
        select(Customer)
        .where(
            Customer.business_id == business_id,
            or_(*filters),
        )
        .order_by(desc(Customer.created_at))
        .limit(limit)
    )
    return list(session.exec(statement).all())


def get_customer_by_phone(
    session: Session,
    business_id: UUID,
    normalized_phone: str,
) -> Optional[Customer]:
    statement = select(Customer).where(
        Customer.business_id == business_id,
        Customer.phone_normalized == normalized_phone,
    )
    return session.exec(statement).first()


def update_customer(session: Session, customer: Customer) -> Customer:
    session.add(customer)
    session.commit()
    session.refresh(customer)
    return customer


def get_customer_summary(
    session: Session,
    business_id: UUID,
    customer_id: UUID,
    recent_activities_limit: int = 10,
) -> dict[str, Any] | None:
    customer = get_customer_by_id(
        session=session,
        business_id=business_id,
        customer_id=customer_id,
    )
    if customer is None:
        return None

    payment_totals_sq = (
        select(
            Payment.invoice_id.label("invoice_id"),
            func.coalesce(func.sum(Payment.amount), 0).label("amount_paid"),
        )
        .where(Payment.business_id == business_id)
        .group_by(Payment.invoice_id)
        .subquery()
    )

    customer_invoices_sq = (
        select(
            Invoice.id.label("invoice_id"),
            Invoice.total_amount.label("total_amount"),
            Invoice.status.label("status"),
            func.coalesce(payment_totals_sq.c.amount_paid, 0).label("amount_paid"),
        )
        .join(Lead, Invoice.lead_id == Lead.id)
        .outerjoin(payment_totals_sq, Invoice.id == payment_totals_sq.c.invoice_id)
        .where(
            Invoice.business_id == business_id,
            Lead.customer_id == customer_id,
            Invoice.status != InvoiceStatus.CANCELLED,
        )
        .subquery()
    )

    lifetime_value = session.exec(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .join(Invoice, Payment.invoice_id == Invoice.id)
        .join(Lead, Invoice.lead_id == Lead.id)
        .where(
            Payment.business_id == business_id,
            Invoice.business_id == business_id,
            Lead.customer_id == customer_id,
        )
    ).one()

    balance_due_expr = customer_invoices_sq.c.total_amount - customer_invoices_sq.c.amount_paid
    total_outstanding = session.exec(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                customer_invoices_sq.c.status.notin_(
                                    [
                                        InvoiceStatus.PAID.value,
                                        InvoiceStatus.DRAFT.value,
                                        InvoiceStatus.CANCELLED.value,
                                    ]
                                ),
                                balance_due_expr > 0,
                            ),
                            balance_due_expr,
                        ),
                        else_=0,
                    )
                ),
                0,
            )
        )
    ).one()

    total_leads = session.exec(
        select(func.count(Lead.id)).where(
            Lead.business_id == business_id,
            Lead.customer_id == customer_id,
        )
    ).one()

    active_leads = session.exec(
        select(func.count(Lead.id))
        .outerjoin(PipelineStage, Lead.stage_id == PipelineStage.id)
        .where(
            Lead.business_id == business_id,
            Lead.customer_id == customer_id,
            or_(
                PipelineStage.id.is_(None),
                func.lower(PipelineStage.name).notin_(["won", "lost"]),
            ),
        )
    ).one()

    total_invoices = session.exec(
        select(func.count()).select_from(customer_invoices_sq)
    ).one()

    upcoming_meetings = session.exec(
        select(func.count(Meeting.id)).where(
            Meeting.business_id == business_id,
            Meeting.customer_id == customer_id,
            Meeting.status == MeetingStatus.SCHEDULED,
            Meeting.scheduled_at >= datetime.now(timezone.utc),
        )
    ).one()

    activity_rows = session.exec(
        select(
            LeadActivity.type,
            LeadActivity.description,
            LeadActivity.created_at,
            Lead.title,
        )
        .join(Lead, LeadActivity.lead_id == Lead.id)
        .where(
            Lead.business_id == business_id,
            Lead.customer_id == customer_id,
        )
        .order_by(desc(LeadActivity.created_at))
        .limit(recent_activities_limit)
    ).all()

    recent_activities = [
        {
            "type": activity_type.value,
            "description": description,
            "date": created_at,
            "lead_title": lead_title,
        }
        for activity_type, description, created_at, lead_title in activity_rows
    ]

    return {
        "customer": customer,
        "lifetime_value": lifetime_value or Decimal("0"),
        "total_outstanding": total_outstanding or Decimal("0"),
        "total_leads": int(total_leads or 0),
        "active_leads": int(active_leads or 0),
        "total_invoices": int(total_invoices or 0),
        "upcoming_meetings": int(upcoming_meetings or 0),
        "recent_activities": recent_activities,
    }
