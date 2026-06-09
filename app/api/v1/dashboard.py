from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.dashboard import (
    AssistantTasksResponse,
    LeadsNeedingActionResponse,
    PaymentSummaryRead,
    TodayActivityResponse,
)
from app.models.user import User
from app.services.dashboard_service import (
    get_assistant_tasks as service_get_assistant_tasks,
    get_leads_needing_action as service_get_leads_needing_action,
    get_payment_summary as service_get_payment_summary,
    get_today_activity as service_get_today_activity,
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
    "/dashboard/leads-needing-action",
    response_model=LeadsNeedingActionResponse,
)
def get_leads_needing_action(
    limit: int = Query(default=10, ge=0, le=50),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> LeadsNeedingActionResponse:
    """Home-screen action list. Returns up to `limit` (default 10, max 50)
    leads sorted by next-action urgency, plus a total count and per-type
    breakdown so the UI can show "+X more" and the secondary counts strip.

    Action set is fixed at cascade types 1-4 (FOLLOWUP_OVERDUE,
    FOLLOWUP_DUE_TODAY, NO_FOLLOWUP_SET, GONE_QUIET) — FOLLOWUP_UPCOMING
    and NONE are excluded by design."""
    return service_get_leads_needing_action(
        session=session,
        current_user=current_user,
        limit=limit,
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


@router.get(
    "/dashboard/today-activity",
    response_model=TodayActivityResponse,
)
def get_today_activity(
    limit: int = Query(default=50, ge=0, le=200),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TodayActivityResponse:
    """The dashboard's bottom-of-page "Today's Activity" diary: the owner's
    high-signal actions today (follow-ups handled, calls/WhatsApp logged,
    new enquiries, payments, invoices, manual stage moves), newest-first.

    Owner actions only (`actor_type = human`) — assistant-driven actions
    live in the separate `/dashboard/assistant-tasks` recently-done bucket.
    `total` may exceed `len(items)` on a busy day; `counts_by_type` and
    `money_collected_today` drive the always-visible summary line."""
    return service_get_today_activity(
        session=session,
        current_user=current_user,
        limit=limit,
    )
