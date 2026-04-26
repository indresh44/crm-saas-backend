from typing import List
from uuid import UUID

from sqlmodel import Session, select

from app.models.payment import Payment


def create_payment(session: Session, payment: Payment) -> Payment:
    session.add(payment)
    session.commit()
    session.refresh(payment)
    return payment


def get_payment_by_id(
    session: Session,
    business_id: UUID,
    payment_id: UUID,
) -> Payment | None:
    statement = select(Payment).where(
        Payment.id == payment_id,
        Payment.business_id == business_id,
    )
    return session.exec(statement).first()


def list_payments_for_business(
    session: Session,
    business_id: UUID,
    *,
    include_voided: bool = False,
) -> List[Payment]:
    statement = select(Payment).where(Payment.business_id == business_id)
    if not include_voided:
        statement = statement.where(Payment.voided_at.is_(None))
    return list(session.exec(statement).all())


def list_payments_for_invoice(
    session: Session,
    business_id: UUID,
    invoice_id: UUID,
    *,
    include_voided: bool = False,
) -> List[Payment]:
    statement = select(Payment).where(
        Payment.business_id == business_id,
        Payment.invoice_id == invoice_id,
    )
    if not include_voided:
        statement = statement.where(Payment.voided_at.is_(None))
    return list(session.exec(statement).all())
