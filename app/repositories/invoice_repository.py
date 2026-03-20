from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem


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
