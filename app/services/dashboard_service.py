from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.core.time_utils import today_in
from app.models.agent_task import AgentTask, TaskStatus
from app.models.dashboard import (
    AssistantTaskSummary,
    AssistantTasksResponse,
    OverdueInvoiceSummary,
    PaymentSummaryRead,
)
from app.models.lead import LeadRead
from app.models.user import User
from app.repositories.business_repository import get_business_by_id
from app.repositories.dashboard_repository import (
    fetch_monthly_collections,
    fetch_outstanding_and_overdue,
)
from app.services.lead_followup_service import list_overdue_followups, list_todays_followups
from app.services.lead_service import list_leads


def _to_float(value: Decimal | int | float) -> float:
    return float(value)


def get_payment_summary(
    session: Session,
    business_id: UUID,
) -> PaymentSummaryRead:
    business = get_business_by_id(session, business_id)
    tz = (business.timezone if business else None) or "Asia/Kolkata"
    today = today_in(tz)
    collections_this_month, collections_last_month = fetch_monthly_collections(
        session=session,
        business_id=business_id,
    )

    outstanding_data = fetch_outstanding_and_overdue(
        session=session,
        business_id=business_id,
        today=today,
    )

    overdue_invoices = [
        OverdueInvoiceSummary(
            invoice_id=str(item["invoice_id"]),
            lead_id=str(item["lead_id"]) if item["lead_id"] is not None else None,
            invoice_number=item["invoice_number"],
            customer_name=item["customer_name"],
            customer_phone=item["customer_phone"],
            total_amount=_to_float(item["total_amount"]),
            amount_paid=_to_float(item["amount_paid"]),
            balance_due=_to_float(item["balance_due"]),
            due_date=item["due_date"],
            days_overdue=(today - item["due_date"]).days,
        )
        for item in outstanding_data["overdue_invoices"]
    ]

    return PaymentSummaryRead(
        collections_this_month=_to_float(collections_this_month),
        collections_last_month=_to_float(collections_last_month),
        total_outstanding=_to_float(outstanding_data["total_outstanding"]),
        outstanding_invoice_count=int(outstanding_data["outstanding_invoice_count"]),
        overdue_invoices=overdue_invoices,
    )


def get_dashboard_summary(
    session: Session,
    current_user: User,
) -> dict:
    todays_followups = list_todays_followups(session=session, current_user=current_user)
    overdue_followups = list_overdue_followups(session=session, current_user=current_user)
    payment_summary = get_payment_summary(session=session, business_id=current_user.business_id)
    recent_leads = list_leads(session=session, current_user=current_user)[:5]

    return {
        "todays_followups_count": len(todays_followups),
        "overdue_followups_count": len(overdue_followups),
        "total_outstanding": payment_summary.total_outstanding,
        "outstanding_invoice_count": payment_summary.outstanding_invoice_count,
        "recent_leads": [_lead_summary(lead) for lead in recent_leads],
    }


def _lead_summary(lead: LeadRead) -> dict:
    return {
        "id": str(lead.id),
        "title": lead.title,
        "customer_name": lead.customer_name,
        "customer_phone": lead.customer_phone,
        "stage_name": lead.stage_name,
        "estimated_value": float(lead.estimated_value) if lead.estimated_value is not None else None,
        "created_at": lead.created_at.date().isoformat(),
    }


# ---------------------------------------------------------------------------
# Assistant-tasks (Phase-1 dashboard section)
# ---------------------------------------------------------------------------

# Hard cap on the recently-done bucket. Defends against a degenerate request
# (`?recently_done_limit=10000`) or a business with thousands of done tasks.
# Frontend defaults to 10; this is the absolute ceiling.
_RECENTLY_DONE_HARD_CAP = 50

# When filtering the recently_done bucket down to "notable" rows we over-
# fetch by this factor. Most read-only Q&A is filtered out, so we need a
# pool larger than the requested limit. 5× covers a session where 1 in
# 5 owner messages does a write; if a business is much chattier, the
# pool may not fill the bucket (acceptable — older notable tasks are off
# the dashboard, the chat history still has them).
_RECENTLY_DONE_OVERFETCH = 5

# Capability → icon category. Keep small and explicit; anything
# unrecognised falls back to "write" (we know it was a committed write
# because the task is notable, just don't know which domain).
_CAPABILITY_ICON: dict[str, str] = {
    "record_payment": "payment",
    "void_payment": "payment",
    "update_payment_metadata": "payment",
    "update_payment_amount": "payment",
    "create_followup": "followup",
    "complete_followup": "followup",
    "reschedule_followup": "followup",
    "cancel_followup": "followup",
    "cancel_invoice": "invoice",
    "add_invoice_adjustment": "invoice",
    "update_invoice": "invoice",
    "create_lead": "lead",
    "update_lead": "lead",
    "update_lead_stage": "lead",
    "add_lead_note": "lead",
    "get_or_create_customer": "customer",
    "update_customer": "customer",
    "create_catalog_item": "catalog",
    "update_catalog_item": "catalog",
    "deactivate_catalog_item": "catalog",
}

# Capability → past-tense verb phrase for the short label. The capability
# name is generally fine but reads awkwardly to non-technical owners
# ("record_payment" vs "Recorded payment"). Used when the LLM's own task
# description is short or uninformative; otherwise the description wins.
_CAPABILITY_VERB: dict[str, str] = {
    "record_payment": "Recorded payment",
    "void_payment": "Voided payment",
    "update_payment_metadata": "Updated payment",
    "update_payment_amount": "Corrected payment amount",
    "create_followup": "Scheduled follow-up",
    "complete_followup": "Marked follow-up done",
    "reschedule_followup": "Rescheduled follow-up",
    "cancel_followup": "Cancelled follow-up",
    "cancel_invoice": "Cancelled invoice",
    "add_invoice_adjustment": "Added invoice adjustment",
    "update_invoice": "Updated invoice",
    "create_lead": "Created lead",
    "update_lead": "Updated lead",
    "update_lead_stage": "Moved lead stage",
    "add_lead_note": "Added lead note",
    "get_or_create_customer": "Linked customer",
    "update_customer": "Updated customer",
    "create_catalog_item": "Created catalog item",
    "update_catalog_item": "Updated catalog item",
    "deactivate_catalog_item": "Deactivated catalog item",
}

# A description shorter than this (after strip) is treated as
# "uninformative" — we prefer the capability-derived label. "try again",
# "do it", "ok" all fall under this.
_UNINFORMATIVE_DESCRIPTION_MAX_CHARS = 16


def get_assistant_tasks(
    session: Session,
    business_id: UUID,
    recently_done_limit: int = 10,
) -> AssistantTasksResponse:
    """Three queries, each on the (business_id, status, updated_at DESC)
    composite index (`ix_agent_tasks_business_status_updated` from
    migration 0041). No JOIN; per-task awaiting-approval data lives in the
    task's own `result` JSONB so the carousel can render every step from
    one endpoint hit — no N+1.

    Tenant scope is enforced by every query (business_id is a leading
    column on the index). Cross-tenant requests return three empty lists."""
    recently_done_limit = max(0, min(recently_done_limit, _RECENTLY_DONE_HARD_CAP))

    running = _fetch_status_bucket(
        session, business_id, TaskStatus.RUNNING.value, limit=None,
    )
    # Both real awaiting-approval rows AND ask_user pauses share
    # status='awaiting_approval' in the DB (the lifecycle column has no
    # finer state). Fetch them in one indexed pass, then split on
    # pending_action_id in Python — cheap, and the bucket is naturally
    # small (in-flight tasks per business at any moment).
    awaiting_pool = _fetch_status_bucket(
        session, business_id, TaskStatus.AWAITING_APPROVAL.value, limit=None,
    )
    awaiting: list[AgentTask] = []
    needs_input: list[AgentTask] = []
    for row in awaiting_pool:
        # The distinguishing fact: real awaiting-approval has a
        # pending_action_id (FK to a real prepared_actions row); ask_user
        # has NULL. Cross-checked against result.kind for defense in
        # depth — a future code path that sets one without the other
        # gets routed by the actual data shape, not by either field
        # alone.
        if row.pending_action_id is not None:
            awaiting.append(row)
        else:
            needs_input.append(row)
    # recently_done = NOTABLE terminal rows only. "Notable" = the task did
    # a write, had a write prepared then cancelled, or failed. Pure
    # read-only Q&A is excluded — the dashboard is for what the assistant
    # DID, not every question asked (the chat history still has those).
    #
    # Filter is done in Python over an OVER-FETCHED pool because the
    # signal lives in `continuity_history` JSONB (presence of a turn with
    # action.type='committed'), which isn't cheap to predicate on in SQL.
    # A partial JSONB-index could be added later if the over-fetch ever
    # costs too much; today it's a handful of rows.
    overfetch_target = max(
        recently_done_limit * _RECENTLY_DONE_OVERFETCH, recently_done_limit,
    )
    done_pool = _fetch_status_bucket(
        session, business_id, TaskStatus.DONE.value, limit=overfetch_target,
    )
    failed_pool = _fetch_status_bucket(
        session, business_id, TaskStatus.FAILED.value, limit=overfetch_target,
    )
    pool = sorted(done_pool + failed_pool,
                  key=lambda r: r.updated_at, reverse=True)

    notable_summaries: list[AssistantTaskSummary] = []
    for row in pool:
        summary = _to_summary(row)
        if summary.notable:
            notable_summaries.append(summary)
        if len(notable_summaries) >= recently_done_limit:
            break

    return AssistantTasksResponse(
        running=[_to_summary(r) for r in running],
        awaiting_approval=[_to_summary(r) for r in awaiting],
        needs_input=[_to_summary(r) for r in needs_input],
        recently_done=notable_summaries,
    )


def _fetch_status_bucket(
    session: Session,
    business_id: UUID,
    status_value: str,
    limit: int | None,
) -> list[AgentTask]:
    """One indexed query: WHERE business_id=? AND status=? ORDER BY
    updated_at DESC [LIMIT n]. Hits ix_agent_tasks_business_status_updated."""
    stmt = (
        select(AgentTask)
        .where(
            AgentTask.business_id == business_id,
            AgentTask.status == status_value,
        )
        .order_by(AgentTask.updated_at.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.exec(stmt).all())


def _to_summary(task: AgentTask) -> AssistantTaskSummary:
    """Flatten an AgentTask row to the dashboard summary shape.

    For awaiting-approval rows we lift `prepared_action_id` / `preview` /
    `editable_fields` out of the `result` JSONB so the frontend doesn't
    have to peek inside payload dicts. For done/failed rows we lift the
    `answer` / `error.message` similarly.

    Phase-1 dashboard polish: also derives `notable`, `icon`, and
    `short_label` so the frontend can render a clean expandable row
    without re-parsing continuity_history client-side."""
    result: dict[str, Any] = task.result or {}
    rk = result.get("kind") if isinstance(result, dict) else None

    prepared_action_id: str | None = None
    preview: str | None = None
    editable_fields: list[str] = []
    answer: str | None = None
    error_message: str | None = None

    if rk == "awaiting_confirm":
        prepared_action_id = (
            str(result.get("prepared_action_id"))
            if result.get("prepared_action_id") else None
        )
        preview = result.get("preview")
        editable_fields = list(result.get("editable_fields") or [])
    elif rk == "done":
        answer = result.get("answer")
    elif rk in ("error", "exhausted"):
        err = result.get("error") or {}
        if isinstance(err, dict):
            error_message = err.get("message") or err.get("code")

    # ---- dashboard derivations ----
    committed_capability = _first_committed_capability(task.continuity_history)
    notable = _is_notable(task, rk, committed_capability)
    icon = _icon_for(committed_capability)
    short_label = _short_label_for(
        task, rk, committed_capability, error_message,
    )

    return AssistantTaskSummary(
        id=task.id,
        session_id=task.session_id,
        batch_id=task.batch_id,
        sequence_index=task.sequence_index,
        description=task.description,
        status=task.status,
        created_at=task.created_at.isoformat(),
        updated_at=task.updated_at.isoformat(),
        prepared_action_id=prepared_action_id,
        preview=preview,
        editable_fields=editable_fields,
        result_kind=rk,
        answer=answer,
        error_message=error_message,
        notable=notable,
        icon=icon,
        short_label=short_label,
    )


def _first_committed_capability(
    continuity_history: list[dict[str, Any]] | None,
) -> str | None:
    """Walk continuity_history for the first turn whose action.type ==
    'committed' (the synth COMMITTED record appended by the chat service
    after a successful /confirm). Returns the capability name on that
    turn, or None if no commit happened.

    The presence/absence of this turn is the canonical "did this task
    write?" signal: the runner only appends a COMMITTED turn when the
    write-surface commit succeeded."""
    if not continuity_history:
        return None
    for turn in continuity_history:
        action = turn.get("action") if isinstance(turn, dict) else None
        if isinstance(action, dict) and action.get("type") == "committed":
            cap = action.get("capability")
            return str(cap) if cap else None
    return None


def _is_notable(
    task: AgentTask,
    result_kind: str | None,
    committed_capability: str | None,
) -> bool:
    """A task belongs in the dashboard's 'recently done' bucket when:
      * it failed (always worth surfacing), OR
      * it did a write (continuity_history contains a committed turn), OR
      * it staged a prepare that the owner then cancelled (apply_cancel_to_task
        sets result.kind='cancelled'), OR
      * it ran any tool — read or prepare — at all (continuity_history is
        non-empty). The loop appends a TurnRecord for every read and every
        prepare; an LLM that answered with no tool use (a bare DONE on
        turn 1) leaves continuity_history empty. The latter is pure chat
        ('ok thanks', 'say hi') and stays filtered out; substantive reads
        ('show partial-paid invoices in a table') now surface here.

    Earlier rule was 'writes only'. Changed because rich read-only answers
    that did real work — multi-task batches, tabular outputs, deliberate
    questions — were being silenced as 'pure read-only Q&A'. The honest
    cut is 'did the assistant DO something' rather than 'did it WRITE
    something'."""
    if task.status == TaskStatus.FAILED.value:
        return True
    if committed_capability is not None:
        return True
    if result_kind == "cancelled":
        return True
    # Lifecycle marker, not real work. The originating ask_user task did
    # no work itself — it was resolved by a follow-up batch which has
    # its own task rows on the dashboard. Surfacing this here would
    # double-count the same conversation.
    if result_kind == "resolved_by_followup":
        return False
    if task.continuity_history:
        return True
    return False


def _icon_for(capability: str | None) -> str:
    if capability is None:
        return "read"
    return _CAPABILITY_ICON.get(capability, "write")


def _short_label_for(
    task: AgentTask,
    result_kind: str | None,
    committed_capability: str | None,
    error_message: str | None,
) -> str:
    """Build the row's one-liner. Strategy:
      * Committed write + uninformative description → capability verb
        (e.g. 'try again' → 'Recorded payment').
      * Committed write + informative description → the description
        (owner's words are the most accurate label).
      * Failed task → the description (so the owner can see what was
        attempted; error detail goes elsewhere).
      * Cancelled prepare → 'Cancelled: <description>'.
      * Anything else (non-notable read; shouldn't end up rendered, but
        defensive) → the description."""
    description = (task.description or "").strip()
    description = _truncate(description, 120)

    if committed_capability is not None:
        verb = _CAPABILITY_VERB.get(committed_capability)
        if verb and _is_uninformative(description):
            return verb
        return description or (verb or "Action")

    if task.status == TaskStatus.FAILED.value:
        return description or "Failed task"

    if result_kind == "cancelled":
        return f"Cancelled: {description}" if description else "Cancelled"

    return description or "Task"


def _is_uninformative(description: str) -> bool:
    """Heuristic. Short text or vague pronouns are uninformative —
    'try again', 'do it', 'ok', 'yes please'. Long descriptions
    (an explicit request) are kept as-is."""
    if len(description) <= _UNINFORMATIVE_DESCRIPTION_MAX_CHARS:
        return True
    return False


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
