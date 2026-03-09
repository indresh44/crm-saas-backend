from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.booking import Booking
from app.models.user import User
from app.repositories.booking_repository import (
    create_booking as repo_create_booking,
    get_booking_by_id,
    list_bookings_for_business,
)
from app.repositories.lead_repository import get_lead_by_id
from app.repositories.quote_repository import get_quote_by_id


def create_booking_from_quote_id(
    session: Session,
    current_user: User,
    quote_id: UUID,
) -> Booking:
    quote = get_quote_by_id(
        session=session,
        business_id=current_user.business_id,
        quote_id=quote_id,
    )
    if quote is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Quote not found for current business",
        )

    lead = get_lead_by_id(
        session=session,
        business_id=current_user.business_id,
        lead_id=quote.lead_id,
    )
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lead not found for current business",
        )

    return repo_create_booking(
        session=session,
        quote=quote,
        lead=lead,
        business_id=current_user.business_id,
    )


def get_booking(
    session: Session,
    current_user: User,
    booking_id: UUID,
) -> Booking:
    booking = get_booking_by_id(
        session=session,
        business_id=current_user.business_id,
        booking_id=booking_id,
    )
    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking not found",
        )
    return booking


def list_bookings(session: Session, current_user: User) -> List[Booking]:
    return list_bookings_for_business(session, business_id=current_user.business_id)

