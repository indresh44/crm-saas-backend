from datetime import date
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from sqlalchemy import and_, case, func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import InvoiceStatus
from app.models.invoice import Invoice
from app.models.invoice import InvoiceListItem
from app.models.invoice_adjustment import InvoiceAdjustment
from app.models.invoice_item import InvoiceItem
from app.models.lead import Lead
from app.models.payment import Payment


def create_invoice(session: Session, invoice: Invoice) -> Invoice:
    session.add(invoice)
    session.commit()
    session.refresh(invoice)
    return invoice


def update_invoice(session: Session, invoice: Invoice) -> Invoice:
    session.add(invoice)
    session.commit()
    session.refresh(invoice)
    return invoice


def get_invoice_by_id(
    session: Session,
    business_id: UUID,
    invoice_id: UUID,
) -> Optional[Invoice]:
    statement = select(Invoice).where(
        Invoice.id == invoice_id,
        Invoice.business_id == business_id,
    )
    return session.exec(statement).first()


def get_invoice_with_items(
    session: Session,
    business_id: UUID,
    invoice_id: UUID,
) -> Invoice | None:
    statement = (
        select(Invoice)
        .options(selectinload(Invoice.items))
        .where(
            Invoice.id == invoice_id,
            Invoice.business_id == business_id,
        )
    )
    return session.exec(statement).first()


def list_invoices_for_business(session: Session, business_id: UUID) -> List[Invoice]:
    statement = select(Invoice).where(Invoice.business_id == business_id)
    return list(session.exec(statement).all())


def list_invoices_for_business_with_items(session: Session, business_id: UUID) -> List[Invoice]:
    statement = (
        select(Invoice)
        .options(selectinload(Invoice.items))
        .where(Invoice.business_id == business_id)
    )
    return list(session.exec(statement).all())


def list_invoices_for_lead(
    session: Session,
    business_id: UUID,
    lead_id: UUID,
) -> List[Invoice]:
    statement = select(Invoice).where(
        Invoice.business_id == business_id,
        Invoice.lead_id == lead_id,
    )
    return list(session.exec(statement).all())


def list_invoices_for_lead_with_items(
    session: Session,
    business_id: UUID,
    lead_id: UUID,
) -> List[Invoice]:
    statement = (
        select(Invoice)
        .options(selectinload(Invoice.items))
        .where(
            Invoice.business_id == business_id,
            Invoice.lead_id == lead_id,
        )
    )
    return list(session.exec(statement).all())


def list_items_for_invoice(session: Session, invoice_id: UUID) -> List[InvoiceItem]:
    statement = select(InvoiceItem).where(InvoiceItem.invoice_id == invoice_id)
    return list(session.exec(statement).all())


def replace_invoice_items(
    session: Session,
    invoice_id: UUID,
    items: List[InvoiceItem],
) -> List[InvoiceItem]:
    existing_items = list_items_for_invoice(session, invoice_id)
    for item in existing_items:
        session.delete(item)
    session.flush()

    for item in items:
        session.add(item)

    session.commit()

    return list_items_for_invoice(session, invoice_id)


def get_invoice_by_quote_id(session: Session, business_id: UUID, quote_id: UUID) -> Invoice | None:
    """Check if an invoice already exists for a given quote."""
    statement = select(Invoice).where(
        Invoice.business_id == business_id,
        Invoice.quote_id == quote_id,
    )
    return session.exec(statement).first()


def list_invoices_enriched(
    session: Session,
    business_id: UUID,
    customer_id: UUID | None = None,
    status: InvoiceStatus | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    lead_id: UUID | None = None,
    limit: int = 20,
    offset: int = 0,
    exclude_draft: bool = False,
    include_cancelled: bool = False,
) -> tuple[list[InvoiceListItem], int, dict[str, Decimal | int]]:
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

    statement = (
        select(
            Invoice,
            func.coalesce(payment_totals_sq.c.amount_paid, 0).label("amount_paid"),
            func.coalesce(adjustment_totals_sq.c.adjustments_total, 0).label("adjustments_total"),
            Customer.name.label("customer_name"),
            Customer.phone.label("customer_phone"),
            Lead.title.label("lead_title"),
        )
        .outerjoin(payment_totals_sq, Invoice.id == payment_totals_sq.c.invoice_id)
        .outerjoin(adjustment_totals_sq, Invoice.id == adjustment_totals_sq.c.invoice_id)
        .outerjoin(Lead, Invoice.lead_id == Lead.id)
        .outerjoin(Customer, Lead.customer_id == Customer.id)
        .where(Invoice.business_id == business_id)
    )

    if customer_id is not None:
        statement = statement.where(Customer.id == customer_id)

    if status is not None:
        statement = statement.where(Invoice.status == status)
    else:
        if exclude_draft:
            statement = statement.where(Invoice.status != InvoiceStatus.DRAFT)
        if not include_cancelled:
            statement = statement.where(Invoice.status != InvoiceStatus.CANCELLED)

    if from_date is not None:
        statement = statement.where(Invoice.issued_date >= from_date)

    if to_date is not None:
        statement = statement.where(Invoice.issued_date <= to_date)

    if lead_id is not None:
        statement = statement.where(Invoice.lead_id == lead_id)

    filtered_sq = statement.order_by(None).subquery()
    total = session.exec(select(func.count()).select_from(filtered_sq)).one()

    balance_due_expr = (
        filtered_sq.c.total_amount
        - filtered_sq.c.amount_paid
        - filtered_sq.c.adjustments_total
    )
    summary_row = session.exec(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                filtered_sq.c.status.notin_(
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
            ).label("total_outstanding"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                filtered_sq.c.status.notin_(
                                    [
                                        InvoiceStatus.PAID.value,
                                        InvoiceStatus.DRAFT.value,
                                        InvoiceStatus.CANCELLED.value,
                                    ]
                                ),
                                balance_due_expr > 0,
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("outstanding_count"),
        )
    ).one()

    rows = session.exec(
        statement.order_by(Invoice.issued_date.desc()).offset(offset).limit(limit)
    ).all()

    invoices: list[InvoiceListItem] = []
    for row in rows:
        invoice, amount_paid, _adjustments_total, customer_name, customer_phone, lead_title = row
        invoice_read = InvoiceListItem.model_validate(invoice, from_attributes=True)
        invoice_read.amount_paid = amount_paid
        invoice_read.customer_name = customer_name
        invoice_read.customer_phone = customer_phone
        invoice_read.lead_title = lead_title
        invoices.append(invoice_read)

    return (
        invoices,
        total,
        {
            "total_outstanding": summary_row.total_outstanding,
            "outstanding_count": int(summary_row.outstanding_count),
        },
    )


def get_invoice_public(
    session: Session,
    invoice_id: UUID,
) -> tuple[Invoice, str | None, str] | None:
    """Fetch invoice by UUID with customer name and business name (no auth)."""
    # Step 1: fetch invoice with items
    inv_stmt = (
        select(Invoice)
        .options(selectinload(Invoice.items))
        .where(Invoice.id == invoice_id)
    )
    invoice = session.exec(inv_stmt).first()
    if invoice is None:
        return None

    # Step 2: resolve customer name via lead
    customer_name: str | None = None
    if invoice.lead_id is not None:
        row = session.exec(
            select(Customer.name)
            .join(Lead, Lead.customer_id == Customer.id)
            .where(Lead.id == invoice.lead_id)
        ).first()
        if row is not None:
            customer_name = row

    # Step 3: resolve business name
    business_name = ""
    biz_row = session.exec(
        select(Business.name).where(Business.id == invoice.business_id)
    ).first()
    if biz_row is not None:
        business_name = biz_row

    return invoice, customer_name, business_name
