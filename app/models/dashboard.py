from datetime import date
from typing import Optional
from uuid import UUID

from sqlmodel import SQLModel

from app.models.lead import LeadRead
from app.models.lead_followup import LeadFollowupRead


class OverdueInvoiceSummary(SQLModel):
    invoice_id: str
    lead_id: str | None = None
    invoice_number: str
    customer_name: str | None = None
    customer_phone: str | None = None
    total_amount: float
    amount_paid: float
    balance_due: float
    due_date: date
    days_overdue: int


class PaymentSummaryRead(SQLModel):
    collections_this_month: float
    collections_last_month: float
    total_outstanding: float
    outstanding_invoice_count: int
    overdue_invoices: list[OverdueInvoiceSummary]


class LeadNeedingActionRead(LeadRead):
    """LeadRead enriched with the lead's open pending follow-up, if any.

    Dashboard-only — added so the home action list can render attempt-streak
    badges, last-outcome, and the scheduled-at time without an N+1 fan-out
    per card. `null` for cascade types that legitimately have no pending
    follow-up (NO_FOLLOWUP_SET, and GONE_QUIET rows whose pending was
    cancelled). Kept as a subclass of `LeadRead` so callers and frontends
    that don't care continue to read the lead fields untouched."""

    open_followup: Optional[LeadFollowupRead] = None


class LeadsNeedingActionResponse(SQLModel):
    """Home-screen "what should I do next" payload.

    `items` is the top-N (default 10) urgent enquiries — already sorted by
    cascade priority then relevant_date. `total` is the full count across
    all action types (1-4) so the UI can render "+X more". `counts_by_type`
    breaks `total` down by cascade type for the optional secondary strip
    (e.g. "4 overdue · 2 today · 7 no follow-up · 3 quiet")."""

    items: list[LeadNeedingActionRead]
    total: int
    counts_by_type: dict[str, int]


# ---------------------------------------------------------------------------
# Assistant-tasks dashboard section (Phase-1 build)
# ---------------------------------------------------------------------------

class AssistantTaskSummary(SQLModel):
    """One agent_tasks row, flattened for the dashboard.

    Awaiting-approval rows carry `prepared_action_id` / `preview` /
    `editable_fields` populated directly from `agent_tasks.result` JSONB so
    the approval carousel does NOT need a per-task fetch — one endpoint call
    is enough for the whole batch of awaiting tasks. `session_id` is here
    because the carousel needs it to route confirm/cancel through the
    existing per-session `/api/v1/agent-chat/sessions/{id}/confirm`."""

    id: UUID
    session_id: UUID
    batch_id: UUID
    sequence_index: int
    description: str
    status: str   # queued | running | awaiting_approval | done | failed
    created_at: str
    updated_at: str

    # Populated for awaiting-approval rows whose result.kind == "awaiting_confirm".
    # All NULL for ask_user / running / done / failed rows; the carousel's
    # graceful-degradation path kicks in when prepared_action_id is NULL.
    prepared_action_id: Optional[str] = None
    preview: Optional[str] = None
    editable_fields: list[str] = []

    # Mirrors task.result["kind"] for terminal rows so the frontend doesn't
    # have to peek into a JSONB payload to label the row.
    result_kind: Optional[str] = None
    answer: Optional[str] = None             # for kind=done
    error_message: Optional[str] = None      # for kind=error / exhausted

    # ---- Dashboard presentation fields (Phase-1 polish) ----
    # Derived server-side so the frontend can render a clean expandable row
    # without re-parsing the task's continuity history.

    # True when the task is worth surfacing on the dashboard's
    # "recently done" list: did a write, prepared+got cancelled, or
    # failed. Pure read-only Q&A (status=done with no `committed` turn in
    # continuity_history) is NOT notable — the chat history still has it,
    # but the dashboard panel is for "what the assistant DID", not every
    # question asked.
    notable: bool = False

    # Coarse category used by the frontend to pick an icon. One of:
    #   "payment" | "followup" | "invoice" | "lead" | "customer" |
    #   "catalog" | "write" | "read"
    # "write" is the catch-all for a committed action whose capability
    # doesn't fit the per-domain buckets. "read" is the default for
    # non-notable rows (will be filtered out of recently_done but the
    # icon is still set in case a future surface displays read tasks).
    icon: str = "read"

    # One-liner the dashboard row shows in its collapsed state. Prefers a
    # clean derived description over the literal owner text when the
    # latter is uninformative ("try again" → "Recorded payment ₹3,000 on
    # INV-008"). Falls back to the task's own description.
    short_label: str = ""


class AssistantTasksResponse(SQLModel):
    """Four-bucket grouping returned by GET /dashboard/assistant-tasks.

    `awaiting_approval` and `needs_input` were ONE bucket before this fix
    — both share `status='awaiting_approval'` in the DB because the
    lifecycle column has no finer state. They are split here by
    `pending_action_id`: real prepared writes (NOT NULL) go to
    awaiting_approval; ask_user pauses (NULL) go to needs_input. The
    frontend renders them as distinct groups — approvals get the
    carousel; questions get an 'Answer in chat' / 'Dismiss' affordance.

    Recently-done is bounded server-side; running and awaiting buckets
    are naturally bounded (in flight at any moment is small)."""

    running: list[AssistantTaskSummary]
    awaiting_approval: list[AssistantTaskSummary]
    needs_input: list[AssistantTaskSummary] = []   # ask_user pauses
    recently_done: list[AssistantTaskSummary]
