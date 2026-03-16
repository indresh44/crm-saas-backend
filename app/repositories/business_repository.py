from uuid import UUID

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
