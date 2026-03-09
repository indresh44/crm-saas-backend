from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.booking import BookingRead
from app.models.user import User
from app.services.booking_service import (
    create_booking_from_quote_id as service_create_booking_from_quote_id,
    get_booking as service_get_booking,
    list_bookings as service_list_bookings,
)

router = APIRouter()


@router.post("/bookings", response_model=BookingRead)
def create_booking(
    quote_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> BookingRead:
    booking = service_create_booking_from_quote_id(
        session=session,
        current_user=current_user,
        quote_id=quote_id,
    )
    return booking


@router.get("/bookings", response_model=List[BookingRead])
def list_bookings(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[BookingRead]:
    bookings = service_list_bookings(session=session, current_user=current_user)
    return bookings


@router.get("/bookings/{booking_id}", response_model=BookingRead)
def get_booking(
    booking_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> BookingRead:
    booking = service_get_booking(
        session=session,
        current_user=current_user,
        booking_id=booking_id,
    )
    return booking

