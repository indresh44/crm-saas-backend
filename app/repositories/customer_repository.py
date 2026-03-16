from typing import List, Optional
from uuid import UUID

from sqlmodel import Session, select

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
    statement = select(Customer).where(
        Customer.business_id == business_id,
        Customer.phone == phone,
    )
    return list(session.exec(statement).all())


def list_customers_for_business(session: Session, business_id: UUID) -> List[Customer]:
    statement = select(Customer).where(Customer.business_id == business_id)
    return list(session.exec(statement).all())


def update_customer(session: Session, customer: Customer) -> Customer:
    session.add(customer)
    session.commit()
    session.refresh(customer)
    return customer

