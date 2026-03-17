from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.customer import (
    CustomerCreateRequest,
    CustomerLookupResponse,
    CustomerOutstandingResponse,
    CustomerRead,
    CustomerSearchResponse,
    CustomerUpdate,
)
from app.models.user import User
from app.services.customer_service import (
    create_customer as service_create_customer,
    get_customer_by_phone as service_get_customer_by_phone,
    get_customer as service_get_customer,
    get_customer_outstanding as service_get_customer_outstanding,
    list_customers as service_list_customers,
    search_customers as service_search_customers,
    update_customer as service_update_customer,
)

router = APIRouter()


@router.post("/customers", response_model=CustomerRead)
def create_customer(
    payload: CustomerCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CustomerRead:
    customer = service_create_customer(session=session, current_user=current_user, data=payload)
    return customer


@router.get("/customers/search", response_model=List[CustomerSearchResponse])
def search_customers(
    query: str = Query(..., min_length=2),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[CustomerSearchResponse]:
    customers = service_search_customers(
        session=session,
        current_user=current_user,
        query=query,
    )
    return customers


@router.get("/customers/by-phone", response_model=CustomerLookupResponse)
def get_customer_by_phone(
    phone: str = Query(..., min_length=1),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CustomerLookupResponse:
    customer = service_get_customer_by_phone(
        session=session,
        current_user=current_user,
        phone=phone,
    )
    return CustomerLookupResponse(found=customer is not None, customer=customer)


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


@router.get("/customers/{customer_id}/outstanding", response_model=CustomerOutstandingResponse)
def get_customer_outstanding(
    customer_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CustomerOutstandingResponse:
    outstanding = service_get_customer_outstanding(
        session=session,
        business_id=current_user.business_id,
        customer_id=customer_id,
    )
    return CustomerOutstandingResponse(**outstanding)


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
