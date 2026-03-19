from typing import List, Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.booking import Booking
from app.models.enums import BookingStatus
from app.models.lead import Lead
from app.models.quote import Quote


def create_booking(
    session: Session,
    quote: Quote,
    lead: Lead,
    business_id: UUID,
) -> Booking:
    booking = Booking(
        lead_id=lead.id,
        quote_id=quote.id,
        business_id=business_id,
        event_date=lead.service_date or quote.created_at.date(),
        total_amount=quote.total_amount,
        status=BookingStatus.CONFIRMED,
    )
    session.add(booking)
    session.commit()
    session.refresh(booking)
    return booking


def get_booking_by_id(
    session: Session,
    business_id: UUID,
    booking_id: UUID,
) -> Optional[Booking]:
    statement = select(Booking).where(
        Booking.id == booking_id,
        Booking.business_id == business_id,
    )
    return session.exec(statement).first()


def list_bookings_for_business(session: Session, business_id: UUID) -> List[Booking]:
    statement = select(Booking).where(Booking.business_id == business_id)
    return list(session.exec(statement).all())
