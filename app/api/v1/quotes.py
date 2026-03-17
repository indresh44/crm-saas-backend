from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.quote import QuoteRead
from app.models.quote_item import QuoteItemRead
from app.models.user import User
from app.services.quote_service import (
    QuoteCreateWithItems,
    QuoteUpdateWithItems,
    create_quote as service_create_quote,
    get_quote as service_get_quote,
    list_quote_items as service_list_quote_items,
    update_quote as service_update_quote,
)

router = APIRouter()


@router.post("/quotes", response_model=QuoteRead)
def create_quote(
    payload: QuoteCreateWithItems,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> QuoteRead:
    quote = service_create_quote(
        session=session,
        current_user=current_user,
        data=payload,
    )
    return quote


@router.get("/quotes/{quote_id}", response_model=QuoteRead)
def get_quote(
    quote_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> QuoteRead:
    quote = service_get_quote(
        session=session,
        current_user=current_user,
        quote_id=quote_id,
    )
    return quote


@router.patch("/quotes/{quote_id}", response_model=QuoteRead)
def update_quote(
    quote_id: UUID,
    payload: QuoteUpdateWithItems,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> QuoteRead:
    quote = service_update_quote(
        session=session,
        current_user=current_user,
        quote_id=quote_id,
        data=payload,
    )
    return quote


@router.get("/quotes/{quote_id}/items", response_model=list[QuoteItemRead])
def list_quote_items(
    quote_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[QuoteItemRead]:
    _ = service_get_quote(
        session=session,
        current_user=current_user,
        quote_id=quote_id,
    )
    return service_list_quote_items(session, quote_id=quote_id)
