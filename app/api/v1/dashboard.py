from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.dashboard import AssistantTasksResponse, PaymentSummaryRead
from app.models.user import User
from app.services.dashboard_service import (
    get_assistant_tasks as service_get_assistant_tasks,
    get_payment_summary as service_get_payment_summary,
)

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


@router.get(
    "/dashboard/assistant-tasks",
    response_model=AssistantTasksResponse,
)
def get_assistant_tasks(
    recently_done_limit: int = Query(default=10, ge=0, le=50),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AssistantTasksResponse:
    """Three buckets (running / awaiting_approval / recently_done) of
    agent_tasks for this business, indexed-hit per bucket. The
    awaiting-approval bucket carries enough fields per row for the
    approval carousel to render every step without a second fetch."""
    return service_get_assistant_tasks(
        session=session,
        business_id=current_user.business_id,
        recently_done_limit=recently_done_limit,
    )
