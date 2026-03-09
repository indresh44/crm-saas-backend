from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.enums import QuoteStatus
from app.models.lead import Lead
from app.models.quote import Quote, QuoteCreate, QuoteItem
from app.models.user import User
from app.repositories.booking_repository import create_booking as repo_create_booking
from app.repositories.lead_repository import get_lead_by_id
from app.repositories.quote_repository import (
    create_quote as repo_create_quote,
    get_quote_by_id,
    list_items_for_quote,
    replace_quote_items,
    update_quote as repo_update_quote,
)


@dataclass
class QuoteItemInput:
    name: str
    quantity: Decimal
    price: Decimal

    @property
    def total(self) -> Decimal:
        return self.quantity * self.price


def _build_quote_items(
    quote_id: UUID,
    items: List[QuoteItemInput],
) -> List[QuoteItem]:
    return [
        QuoteItem(
            quote_id=quote_id,
            name=item.name,
            quantity=item.quantity,
            price=item.price,
            total=item.total,
        )
        for item in items
    ]


def create_quote(
    session: Session,
    current_user: User,
    data: "QuoteCreateWithItems",
) -> Quote:
    lead = get_lead_by_id(
        session=session,
        business_id=current_user.business_id,
        lead_id=data.lead_id,
    )
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lead not found for current business",
        )

    quote_data = data.quote.model_dump()
    quote_data["business_id"] = current_user.business_id
    quote = Quote(**quote_data)
    quote = repo_create_quote(session, quote)

    items = _build_quote_items(
        quote_id=quote.id,
        items=[QuoteItemInput(**asdict(item)) for item in data.items],
    )
    replace_quote_items(session, quote_id=quote.id, items=items)

    return quote


def get_quote(
    session: Session,
    current_user: User,
    quote_id: UUID,
) -> Quote:
    quote = get_quote_by_id(
        session=session,
        business_id=current_user.business_id,
        quote_id=quote_id,
    )
    if quote is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quote not found",
        )
    return quote


def list_quote_items(session: Session, quote_id: UUID) -> List[QuoteItem]:
    return list_items_for_quote(session, quote_id)


def update_quote(
    session: Session,
    current_user: User,
    quote_id: UUID,
    data: "QuoteUpdateWithItems",
) -> Quote:
    quote = get_quote(session, current_user, quote_id)

    previous_status = quote.status

    update_data = data.quote.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(quote, field, value)

    quote = repo_update_quote(session, quote)

    if data.items is not None:
        items = _build_quote_items(
            quote_id=quote.id,
            items=[QuoteItemInput(**asdict(item)) for item in data.items],
        )
        replace_quote_items(session, quote_id=quote.id, items=items)

    if previous_status != QuoteStatus.ACCEPTED and quote.status == QuoteStatus.ACCEPTED:
        _ensure_booking_for_quote(session, current_user, quote)

    return quote


def _ensure_booking_for_quote(
    session: Session,
    current_user: User,
    quote: Quote,
) -> None:
    lead: Lead | None = get_lead_by_id(
        session=session,
        business_id=current_user.business_id,
        lead_id=quote.lead_id,
    )
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lead for quote not found in current business",
        )

    booking = repo_create_booking(
        session=session,
        quote=quote,
        lead=lead,
        business_id=current_user.business_id,
    )
    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create booking from accepted quote",
        )


from dataclasses import dataclass  # noqa: E402  (re-import for type-only use)
from typing import Optional  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402


class QuoteData(SQLModel):
    lead_id: UUID
    title: str
    description: Optional[str] = None
    total_amount: Decimal
    status: QuoteStatus = QuoteStatus.DRAFT


@dataclass
class QuoteItemPayload:
    name: str
    quantity: Decimal
    price: Decimal


class QuoteCreateWithItems(SQLModel):
    quote: QuoteData
    items: List[QuoteItemPayload]


class QuoteUpdateWithItems(SQLModel):
    quote: QuoteData
    items: Optional[List[QuoteItemPayload]] = None

