from typing import List, Optional
from uuid import UUID

from sqlalchemy import desc, or_
from sqlmodel import Session, select

from app.core.phone import normalize_phone_value
from app.models.customer import Customer


def create_customer(session: Session, customer: Customer) -> Customer:
    session.add(customer)
    session.commit()
    session.refresh(customer)
    return customer


def get_customer_by_id(
    session: Session,
    business_id: UUID,
    customer_id: UUID,
) -> Optional[Customer]:
    statement = select(Customer).where(
        Customer.id == customer_id,
        Customer.business_id == business_id,
    )
    return session.exec(statement).first()


def get_customers_by_phone(
    session: Session,
    business_id: UUID,
    phone: str,
) -> List[Customer]:
    """Return customers matching the given phone (for auto-linking conversations)."""
    normalized_phone = normalize_phone_value(phone)
    statement = select(Customer).where(
        Customer.business_id == business_id,
        Customer.phone_normalized == normalized_phone,
    )
    return list(session.exec(statement).all())


def list_customers_for_business(session: Session, business_id: UUID) -> List[Customer]:
    statement = select(Customer).where(Customer.business_id == business_id)
    return list(session.exec(statement).all())


def search_customers(
    session: Session,
    business_id: UUID,
    query: str,
    limit: int = 10,
) -> List[Customer]:
    normalized_query = normalize_phone_value(query)
    filters = [Customer.name.ilike(f"%{query}%")]
    if normalized_query:
        filters.append(Customer.phone_normalized.like(f"%{normalized_query}%"))

    statement = (
        select(Customer)
        .where(
            Customer.business_id == business_id,
            or_(*filters),
        )
        .order_by(desc(Customer.created_at))
        .limit(limit)
    )
    return list(session.exec(statement).all())


def get_customer_by_phone(
    session: Session,
    business_id: UUID,
    normalized_phone: str,
) -> Optional[Customer]:
    statement = select(Customer).where(
        Customer.business_id == business_id,
        Customer.phone_normalized == normalized_phone,
    )
    return session.exec(statement).first()


def update_customer(session: Session, customer: Customer) -> Customer:
    session.add(customer)
    session.commit()
    session.refresh(customer)
    return customer
