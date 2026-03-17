from typing import List, Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.quote import Quote
from app.models.quote_item import QuoteItem


def create_quote(session: Session, quote: Quote) -> Quote:
    session.add(quote)
    session.commit()
    session.refresh(quote)
    return quote


def update_quote(session: Session, quote: Quote) -> Quote:
    session.add(quote)
    session.commit()
    session.refresh(quote)
    return quote


def get_quote_by_id(
    session: Session,
    business_id: UUID,
    quote_id: UUID,
) -> Optional[Quote]:
    statement = select(Quote).where(
        Quote.id == quote_id,
        Quote.business_id == business_id,
    )
    return session.exec(statement).first()


def list_quotes_for_business(session: Session, business_id: UUID) -> List[Quote]:
    statement = select(Quote).where(Quote.business_id == business_id)
    return list(session.exec(statement).all())


def list_items_for_quote(session: Session, quote_id: UUID) -> List[QuoteItem]:
    statement = select(QuoteItem).where(QuoteItem.quote_id == quote_id)
    return list(session.exec(statement).all())


def replace_quote_items(
    session: Session,
    quote_id: UUID,
    items: List[QuoteItem],
) -> List[QuoteItem]:
    existing_items = list_items_for_quote(session, quote_id)
    for item in existing_items:
        session.delete(item)
    session.flush()

    for item in items:
        session.add(item)

    session.commit()

    return list_items_for_quote(session, quote_id)
