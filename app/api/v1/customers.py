from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.customer import CustomerCreate, CustomerRead
from app.models.user import User
from app.services.customer_service import (
    CustomerUpdate,
    create_customer as service_create_customer,
    get_customer as service_get_customer,
    list_customers as service_list_customers,
    update_customer as service_update_customer,
)

router = APIRouter()


@router.post("/customers", response_model=CustomerRead)
def create_customer(
    payload: CustomerCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CustomerRead:
    customer = service_create_customer(session=session, current_user=current_user, data=payload)
    return customer


@router.get("/customers", response_model=List[CustomerRead])
def list_customers(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[CustomerRead]:
    customers = service_list_customers(session=session, current_user=current_user)
    return customers


@router.get("/customers/{customer_id}", response_model=CustomerRead)
def get_customer(
    customer_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CustomerRead:
    customer = service_get_customer(session=session, current_user=current_user, customer_id=customer_id)
    return customer


@router.patch("/customers/{customer_id}", response_model=CustomerRead)
def update_customer(
    customer_id: UUID,
    payload: CustomerUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CustomerRead:
    customer = service_update_customer(
        session=session,
        current_user=current_user,
        customer_id=customer_id,
        data=payload,
    )
    return customer
