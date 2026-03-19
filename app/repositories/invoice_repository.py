from typing import List, Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.invoice import Invoice


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


def list_invoices_for_business(session: Session, business_id: UUID) -> List[Invoice]:
    statement = select(Invoice).where(Invoice.business_id == business_id)
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


def get_invoice_by_quote_id(session: Session, business_id: UUID, quote_id: UUID) -> Invoice | None:
    """Check if an invoice already exists for a given quote."""
    statement = select(Invoice).where(
        Invoice.business_id == business_id,
        Invoice.quote_id == quote_id,
    )
    return session.exec(statement).first()
