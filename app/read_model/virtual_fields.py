"""Read-model virtual-field resolvers.

This is where ``canonical-definitions-v1.md`` (v1-final) becomes SQL. Each virtual
field declared in ``schema.py`` is turned into a real SQLAlchemy expression usable
in SELECT, WHERE, and ORDER BY, so the assistant can filter and sort on it — not
just read it.

The canonical doc is the single source of truth. Every formula, edge case, and
mapping below cites the governing section. Do not alter a definition here; change
the doc first (a later, separate task).

Tenant scoping inside the sub-queries/joins these resolvers introduce:
  - Where the dependent table HAS ``business_id`` (``payments``), the predicate is
    injected explicitly AND the sub-query is correlated to the (already
    business-scoped) root row.
  - Where it does NOT (``invoice_adjustments``, ``lead_followups``,
    ``pipeline_stages``), there is no column to inject; scope reaches in via
    correlation to the root entity row, which the compiler has already filtered to
    ``business_id``. So a cross-tenant child row can never be summed/matched.

Time-relative fields take a single injected clock (``ctx.today`` / ``ctx.now``,
business-local) — never a scattered ``date.today()``. The compiler injects it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional
from uuid import UUID

from sqlalchemy import Integer, and_, case, cast, func, literal, not_, select as sa_select

from app.models.enums import InvoiceStatus
from app.models.invoice import Invoice
from app.models.invoice_adjustment import InvoiceAdjustment
from app.models.lead import Lead
from app.models.lead_followup import LeadFollowup
from app.models.payment import Payment
from app.models.pipeline import PipelineStage


# ---------------------------------------------------------------------------
# Resolver context + registry types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolverContext:
    """Everything a resolver needs to build its expression.

    ``today`` / ``now`` are business-local and may be None for non-time-relative
    fields; the compiler guarantees they are present whenever a resolver with
    ``needs_clock=True`` is used.
    """

    entity: str
    model: type
    business_id: UUID
    today: Optional[date]
    now: Optional[datetime]


@dataclass(frozen=True)
class VirtualResolver:
    requires_joins: tuple[str, ...]   # schema join names the field depends on
    needs_clock: bool                 # True if it reads ctx.today / ctx.now
    build: Callable[[ResolverContext], object]


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------

# Stage names treated as terminal (won/lost), per canonical §10. Matched
# case-insensitively against the lowered stage name.
_TERMINAL_STAGE_NAMES = ("won", "lost", "closed won", "closed lost")

# Statuses that make an invoice a TAX INVOICE (else ESTIMATE), per canonical §3.
_TAX_INVOICE_STATUSES = [
    InvoiceStatus.APPROVED,
    InvoiceStatus.PARTIAL,
    InvoiceStatus.PAID,
    InvoiceStatus.CANCELLED,
]

# Statuses excluded from "overdue", per canonical §5.
_NOT_OVERDUE_STATUSES = [InvoiceStatus.PAID, InvoiceStatus.CANCELLED, InvoiceStatus.DRAFT]


def _active_payments_sum(ctx: ResolverContext):
    """SUM of active (non-voided) payments for the correlated invoice.

    Tenant scope: ``payments`` has ``business_id`` -> inject it explicitly, AND
    correlate to the (scoped) invoice. ``voided_at IS NULL`` per canonical §1.
    """
    return (
        sa_select(func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.invoice_id == Invoice.id)
        .where(Payment.voided_at.is_(None))
        .where(Payment.business_id == ctx.business_id)
        .scalar_subquery()
    )


def _adjustments_sum(ctx: ResolverContext):
    """SUM of adjustments (discount + write_off) for the correlated invoice.

    Tenant scope: ``invoice_adjustments`` has NO ``business_id`` column, so scope
    reaches in only via correlation to the (already business-scoped) invoice.
    """
    return (
        sa_select(func.coalesce(func.sum(InvoiceAdjustment.amount), 0))
        .where(InvoiceAdjustment.invoice_id == Invoice.id)
        .scalar_subquery()
    )


def _balance_expr(ctx: ResolverContext):
    """Canonical §1 balance: total_amount - active payments - adjustments. Signed
    (NOT clamped). ONE shared builder; is_overdue and days_overdue reuse it."""
    return Invoice.total_amount - _active_payments_sum(ctx) - _adjustments_sum(ctx)


def _is_overdue_expr(ctx: ResolverContext):
    """Canonical §5: due_date < today AND status NOT IN {paid,cancelled,draft}
    AND balance > 0. Time-relative (business-local today)."""
    return and_(
        Invoice.due_date < ctx.today,
        Invoice.status.notin_(_NOT_OVERDUE_STATUSES),
        _balance_expr(ctx) > 0,
    )


# ---------------------------------------------------------------------------
# Per-field expression builders
# ---------------------------------------------------------------------------

def _build_balance(ctx: ResolverContext):
    return _balance_expr(ctx)


def _build_is_overdue(ctx: ResolverContext):
    return _is_overdue_expr(ctx)


def _build_days_overdue(ctx: ResolverContext):
    """Canonical §6: (today - due_date) when overdue, else NULL. Postgres
    date - date yields an integer day count; cast keeps the type explicit. NULL
    when not overdue -> filters use SQL three-valued logic (e.g. ``> 30`` excludes
    nulls without error)."""
    return case(
        (_is_overdue_expr(ctx), cast(literal(ctx.today) - Invoice.due_date, Integer)),
        else_=literal(None),
    )


def _build_document_type(ctx: ResolverContext):
    """Canonical §3: draft/sent -> 'estimate'; approved/partial/paid/cancelled ->
    'tax_invoice'. Pure status CASE."""
    return case(
        (Invoice.status.in_(_TAX_INVOICE_STATUSES), literal("tax_invoice")),
        else_=literal("estimate"),
    )


def _build_is_cancelled(ctx: ResolverContext):
    """Canonical §4: status == cancelled."""
    return Invoice.status == InvoiceStatus.CANCELLED


def _build_stage_name(ctx: ResolverContext):
    """Canonical §9: the joined pipeline stage name (needs pipeline_stage join)."""
    return PipelineStage.name


def _build_stage_color(ctx: ResolverContext):
    """Canonical §9: the joined pipeline stage color (needs pipeline_stage join)."""
    return PipelineStage.color


def _build_is_terminal(ctx: ResolverContext):
    """Canonical §10: lower(stage_name) in {won,lost,closed won,closed lost}.
    COALESCE(..., false): an unknown/missing stage name is NOT terminal (founder
    decision)."""
    return func.coalesce(func.lower(PipelineStage.name).in_(_TERMINAL_STAGE_NAMES), literal(False))


def _build_is_active(ctx: ResolverContext):
    """Canonical §10: NOT is_terminal. Never null (is_terminal is COALESCEd)."""
    return not_(_build_is_terminal(ctx))


def _build_has_overdue_followup(ctx: ResolverContext):
    """Canonical §11: EXISTS a pending follow-up with scheduled_at < now.

    Tenant scope: ``lead_followups`` has no ``business_id``; scope reaches via
    correlation to the (business-scoped) lead. Time-relative (business-local now).
    """
    return (
        sa_select(literal(1))
        .where(LeadFollowup.lead_id == Lead.id)
        .where(LeadFollowup.status == "pending")
        .where(LeadFollowup.scheduled_at < ctx.now)
        .exists()
    )


# --- Batch 1 virtuals --------------------------------------------------------

def _build_payment_is_voided(ctx: ResolverContext):
    """A payment is voided iff ``voided_at IS NOT NULL``. Boolean. No clock
    needed; void state is a stored flag, not a time-relative computation."""
    return Payment.voided_at.is_not(None)


def _build_followup_is_overdue(ctx: ResolverContext):
    """A follow-up is overdue iff ``status = 'pending' AND scheduled_at < now``.
    Time-relative (needs the injected business-local clock). Distinct from the
    leads-side ``has_overdue_followup`` virtual: this is a per-row flag on the
    follow-up itself."""
    return and_(LeadFollowup.status == "pending", LeadFollowup.scheduled_at < ctx.now)


def _build_followup_is_completed(ctx: ResolverContext):
    """A follow-up is completed iff ``status = 'done'``. Pure status check."""
    return LeadFollowup.status == "done"


# ---------------------------------------------------------------------------
# Registry: (entity, field name) -> resolver
# ---------------------------------------------------------------------------

VIRTUAL_RESOLVERS: dict[tuple[str, str], VirtualResolver] = {
    # Invoices
    ("invoices", "balance"): VirtualResolver((), False, _build_balance),
    ("invoices", "is_overdue"): VirtualResolver((), True, _build_is_overdue),
    ("invoices", "days_overdue"): VirtualResolver((), True, _build_days_overdue),
    ("invoices", "document_type"): VirtualResolver((), False, _build_document_type),
    ("invoices", "is_cancelled"): VirtualResolver((), False, _build_is_cancelled),
    # Leads
    ("leads", "stage_name"): VirtualResolver(("pipeline_stage",), False, _build_stage_name),
    ("leads", "stage_color"): VirtualResolver(("pipeline_stage",), False, _build_stage_color),
    ("leads", "is_terminal"): VirtualResolver(("pipeline_stage",), False, _build_is_terminal),
    ("leads", "is_active"): VirtualResolver(("pipeline_stage",), False, _build_is_active),
    ("leads", "has_overdue_followup"): VirtualResolver((), True, _build_has_overdue_followup),
    # Batch 1 — Direct entity virtuals
    ("payments", "is_voided"): VirtualResolver((), False, _build_payment_is_voided),
    # Batch 1 — ViaParent entity virtuals
    ("lead_followups", "is_overdue"): VirtualResolver((), True, _build_followup_is_overdue),
    ("lead_followups", "is_completed"): VirtualResolver((), False, _build_followup_is_completed),
}


def get_resolver(entity: str, field_name: str) -> Optional[VirtualResolver]:
    return VIRTUAL_RESOLVERS.get((entity, field_name))
