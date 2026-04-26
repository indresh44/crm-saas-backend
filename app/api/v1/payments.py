from uuid import UUID
from typing import List

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.payment import (
    PaymentAmountUpdate,
    PaymentCreate,
    PaymentMetadataUpdate,
    PaymentMoveRequest,
    PaymentRead,
    PaymentVoidRequest,
)
from app.models.user import User
from app.services.payment_service import (
    create_payment as service_create_payment,
    list_payments as service_list_payments,
    move_payment_to_invoice as service_move_payment,
    update_payment_amount as service_update_amount,
    update_payment_metadata as service_update_metadata,
    void_payment as service_void_payment,
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
    invoice_id: UUID | None = None,
    include_voided: bool = False,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[PaymentRead]:
    payments = service_list_payments(
        session=session,
        current_user=current_user,
        invoice_id=invoice_id,
        include_voided=include_voided,
    )
    return payments


@router.patch("/payments/{payment_id}", response_model=PaymentRead)
def update_payment(
    payment_id: UUID,
    payload: PaymentMetadataUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PaymentRead:
    """In-place edit of date / method / reference. No money math change."""
    return service_update_metadata(
        session=session,
        current_user=current_user,
        payment_id=payment_id,
        data=payload,
    )


@router.patch("/payments/{payment_id}/amount", response_model=PaymentRead)
def update_payment_amount(
    payment_id: UUID,
    payload: PaymentAmountUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PaymentRead:
    """Amount edit — voids the original and creates a new payment with the
    corrected amount. Returns the new payment."""
    return service_update_amount(
        session=session,
        current_user=current_user,
        payment_id=payment_id,
        data=payload,
    )


@router.patch("/payments/{payment_id}/invoice", response_model=PaymentRead)
def move_payment(
    payment_id: UUID,
    payload: PaymentMoveRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PaymentRead:
    """Move payment to a different invoice (same customer scope)."""
    return service_move_payment(
        session=session,
        current_user=current_user,
        payment_id=payment_id,
        target_invoice_id=payload.invoice_id,
        reason=payload.reason,
    )


@router.post("/payments/{payment_id}/void", response_model=PaymentRead)
def delete_payment(
    payment_id: UUID,
    payload: PaymentVoidRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PaymentRead:
    """Soft-void the payment. Returns the voided payment.

    Modeled as POST /void to match the invoice-cancel pattern — DELETE
    with a JSON body is non-standard and not used elsewhere in this API.
    """
    return service_void_payment(
        session=session,
        current_user=current_user,
        payment_id=payment_id,
        reason=payload.reason,
    )
