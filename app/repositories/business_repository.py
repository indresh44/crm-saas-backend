from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import desc
from sqlmodel import Session, select

from app.models.business import Business


def create_business(session: Session, business: Business) -> Business:
    session.add(business)
    session.commit()
    session.refresh(business)
    return business


def get_business_by_id(session: Session, business_id: UUID) -> Business | None:
    statement = select(Business).where(Business.id == business_id)
    return session.exec(statement).first()


def list_businesses(session: Session) -> list[Business]:
    statement = select(Business).order_by(desc(Business.created_at))
    return list(session.exec(statement).all())


def update_business(session: Session, business: Business) -> Business:
    session.add(business)
    session.commit()
    session.refresh(business)
    return business


def increment_invoice_sequence(session: Session, business_id: UUID) -> int:
    """
    Atomically increments invoice_sequence for the business and
    returns the new value. Uses UPDATE ... RETURNING to avoid
    race conditions.
    """
    result = session.execute(
        sa.text(
            """
            UPDATE businesses
            SET invoice_sequence = invoice_sequence + 1
            WHERE id = :business_id
            RETURNING invoice_sequence
            """
        ),
        {"business_id": str(business_id)},
    ).first()
    session.flush()
    return result[0]
