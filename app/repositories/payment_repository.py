from typing import List
from uuid import UUID

from sqlmodel import Session, select

from app.models.payment import Payment


def create_payment(session: Session, payment: Payment) -> Payment:
    session.add(payment)
    session.commit()
    session.refresh(payment)
    return payment


def list_payments_for_business(session: Session, business_id: UUID) -> List[Payment]:
    statement = select(Payment).where(Payment.business_id == business_id)
    return list(session.exec(statement).all())


def list_payments_for_invoice(
    session: Session,
    business_id: UUID,
    invoice_id: UUID,
) -> List[Payment]:
    statement = select(Payment).where(
        Payment.business_id == business_id,
        Payment.invoice_id == invoice_id,
    )
    return list(session.exec(statement).all())
