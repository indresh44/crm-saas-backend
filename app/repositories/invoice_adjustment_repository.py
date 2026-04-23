"""Data access for invoice_adjustments."""

from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.invoice_adjustment import InvoiceAdjustment


def create_adjustment(session: Session, adjustment: InvoiceAdjustment) -> InvoiceAdjustment:
    session.add(adjustment)
    session.commit()
    session.refresh(adjustment)
    return adjustment


def get_adjustment_by_id(
    session: Session,
    adjustment_id: UUID,
) -> Optional[InvoiceAdjustment]:
    return session.get(InvoiceAdjustment, adjustment_id)


def list_adjustments_for_invoice(
    session: Session,
    invoice_id: UUID,
) -> List[InvoiceAdjustment]:
    statement = (
        select(InvoiceAdjustment)
        .where(InvoiceAdjustment.invoice_id == invoice_id)
        .order_by(InvoiceAdjustment.created_at)
    )
    return list(session.exec(statement).all())


def delete_adjustment(session: Session, adjustment: InvoiceAdjustment) -> None:
    session.delete(adjustment)
    session.commit()


def sum_adjustments_for_invoice(session: Session, invoice_id: UUID) -> Decimal:
    """Return the total reduction applied to an invoice (sum of positive amounts)."""
    total = session.exec(
        select(func.coalesce(func.sum(InvoiceAdjustment.amount), 0)).where(
            InvoiceAdjustment.invoice_id == invoice_id
        )
    ).one()
    return Decimal(str(total or 0))
