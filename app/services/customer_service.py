from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.customer import Customer, CustomerCreate
from app.models.user import User
from app.repositories.customer_repository import (
    create_customer as repo_create_customer,
    get_customer_by_id,
    list_customers_for_business,
    update_customer as repo_update_customer,
)


def create_customer(
    session: Session,
    current_user: User,
    data: CustomerCreate,
) -> Customer:
    customer_data = data.model_dump()
    customer_data["business_id"] = current_user.business_id
    customer = Customer(**customer_data)
    return repo_create_customer(session, customer)


def get_customer(
    session: Session,
    current_user: User,
    customer_id: UUID,
) -> Customer:
    customer = get_customer_by_id(
        session=session,
        business_id=current_user.business_id,
        customer_id=customer_id,
    )
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )
    return customer


def list_customers(session: Session, current_user: User) -> List[Customer]:
    return list_customers_for_business(session, business_id=current_user.business_id)


def update_customer(
    session: Session,
    current_user: User,
    customer_id: UUID,
    data: "CustomerUpdate",
) -> Customer:
    customer = get_customer(session, current_user, customer_id)
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(customer, field, value)
    return repo_update_customer(session, customer)


from app.models.customer import CustomerBase  # noqa: E402  (import after annotations)
from sqlmodel import SQLModel  # noqa: E402
from typing import Optional  # noqa: E402


class CustomerUpdate(SQLModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None

