from typing import List

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.payment import PaymentCreate, PaymentRead
from app.models.user import User
from app.services.payment_service import (
    create_payment as service_create_payment,
    list_payments as service_list_payments,
)

router = APIRouter()


@router.post("/payments", response_model=PaymentRead)
def create_payment(
    payload: PaymentCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PaymentRead:
    payment = service_create_payment(
        session=session,
        current_user=current_user,
        data=payload,
    )
    return payment


@router.get("/payments", response_model=List[PaymentRead])
def list_payments(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[PaymentRead]:
    payments = service_list_payments(session=session, current_user=current_user)
    return payments

