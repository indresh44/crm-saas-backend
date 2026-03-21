from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.dashboard import PaymentSummaryRead
from app.models.user import User
from app.services.dashboard_service import get_payment_summary as service_get_payment_summary

router = APIRouter()


@router.get("/dashboard/payment-summary", response_model=PaymentSummaryRead)
def get_payment_summary(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PaymentSummaryRead:
    return service_get_payment_summary(
        session=session,
        business_id=current_user.business_id,
    )
