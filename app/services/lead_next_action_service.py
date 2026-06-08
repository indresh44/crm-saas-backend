"""Per-lead "next action" cascade — the single source of truth for the
question every screen now answers: *what should I do next for this enquiry?*

Cascade priority (first match wins):
  1. FOLLOWUP_OVERDUE    — pending follow-up, scheduled_at < today (local)
  2. FOLLOWUP_DUE_TODAY  — pending follow-up, scheduled today (local)
  3. NO_FOLLOWUP_SET     — open lead in an early-funnel stage, no upcoming
                           follow-up, recently touched
  4. GONE_QUIET          — open lead in an early-funnel stage, no upcoming
                           follow-up, no human signal for `GONE_QUIET_DAYS`
                           days
  5. FOLLOWUP_UPCOMING   — pending follow-up scheduled for a future date
  6. NONE                — terminal (won/lost/closed) OR a non-early-funnel
                           lead without a pending follow-up (inferred
                           nudges are suppressed off the early funnel —
                           see EARLY_FUNNEL_STAGE_NAMES)

Conditions 3 and 4 share the "no upcoming follow-up" precondition; the cut
between them is the GONE_QUIET threshold against the lead's last human
signal (max of: last human/AI activity, last completed follow-up,
lead.created_at). Without this exclusivity, GONE_QUIET would be unreachable
under strict first-match. See `Docs/plans/next-action-cascade.md` for the
full rationale.

This module is pure compute over already-fetched data — no DB access. The
caller (`lead_service.list_leads`, the dashboard endpoint) hydrates the
inputs in three bulk queries and hands them in. That keeps the cascade
testable without a session and avoids N+1 on the list views.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.time_utils import DEFAULT_TIMEZONE, today_in
from app.models.lead import (
    HOME_NEEDS_ACTION_TYPES,
    LeadRead,
    NextActionSummary,
    NextActionType,
    urgency_rank_for,
)
from app.models.lead_followup import LeadFollowup


# Days of silence (no human signal) before a no-followup lead flips from
# NO_FOLLOWUP_SET ("set a follow-up") to GONE_QUIET ("nudge them"). Locked
# at 5 in `next-action-cascade-decisions.md`; lift to a per-business
# setting only if real usage demands it.
GONE_QUIET_DAYS: int = 5


# Terminal pipeline-stage names — case-insensitive match against
# `lead.stage_name`. Covers the default persona templates from
# `pipeline_service.PIPELINE_TEMPLATES` (`completed`, `lost`) plus the
# common alternative names that the read model and CLAUDE.md reference
# (`won` / `closed won` / `closed lost`). The read model's `is_terminal`
# virtual (`app/read_model/virtual_fields.py:76`) currently has a narrower
# list — known divergence; tracked for a follow-up to align both.
_TERMINAL_STAGE_NAMES: frozenset[str] = frozenset({
    "won", "lost", "closed won", "closed lost",
    "completed",  # success state in persona templates (interior_designer, photographer, coach, other)
})


def _is_terminal_stage(stage_name: Optional[str]) -> bool:
    return (stage_name or "").strip().lower() in _TERMINAL_STAGE_NAMES


# Stages where the *inferred* nudges (NO_FOLLOWUP_SET, GONE_QUIET) fire.
# The two *explicit* follow-up cascade types (OVERDUE, DUE_TODAY) fire on
# every non-terminal stage — silencing those would swallow a follow-up the
# owner explicitly scheduled. See `Docs/plans/next-action-cascade.md` and
# `next-action-decisions.md` for the rationale.
#
# Names match the persona templates in `pipeline_service.PIPELINE_TEMPLATES`
# (default first stage = "New Enquiry") plus shorter synonyms in case a
# custom pipeline uses "New" or similar. Match is case-insensitive after
# trim — mirrors `_is_terminal_stage`.
#
# Hardcoded for v1. The locked decisions doc anticipates lifting this to a
# per-stage `enable_nudges` flag (settings-toggleable) once that surface
# lands. When that happens, this constant becomes a call to
# `pipeline_stage.enable_nudges` and the helper goes away — no callers
# need to change.
EARLY_FUNNEL_STAGE_NAMES: frozenset[str] = frozenset({
    "new enquiry",  # the actual default first stage from PIPELINE_TEMPLATES
    "interested",
    "new",  # synonym — covers custom pipelines that drop the "Enquiry" suffix
})


def _is_early_funnel_stage(stage_name: Optional[str]) -> bool:
    return (stage_name or "").strip().lower() in EARLY_FUNNEL_STAGE_NAMES


def _zone(tz: str) -> ZoneInfo:
    """Resolve an IANA zone string with a lenient fallback to the app
    default (matches `time_utils._as_zone` semantics without depending on
    the private helper)."""
    if not tz:
        return DEFAULT_TIMEZONE
    try:
        return ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        return DEFAULT_TIMEZONE


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLModel pulls TIMESTAMPTZ columns as aware datetimes, but a few
    legacy rows (and tests that build LeadRead directly) land naive. Treat
    naive as UTC — matches the storage convention documented in
    `app/core/time_utils.py`."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _format_short_date(dt: datetime, tz: str) -> str:
    """Short "2 Jun" label for upcoming follow-ups. Cross-platform: avoids
    `%-d` (Linux-only) by stripping the zero-pad manually."""
    local = _as_utc(dt).astimezone(_zone(tz))
    return f"{local.day} {local.strftime('%b')}"


def compute_next_action(
    *,
    lead: LeadRead,
    next_pending_followup: Optional[LeadFollowup],
    last_human_touch: Optional[datetime],
    last_completed_followup: Optional[datetime],
    business_tz: str,
    now_utc: datetime,
) -> NextActionSummary:
    """Cascade for one lead. Inputs are pre-fetched — see module docstring.

    Args:
        lead: The enriched LeadRead (must have `stage_name` populated).
        next_pending_followup: Earliest pending follow-up for this lead,
            or None if no pending follow-up exists.
        last_human_touch: MAX(created_at) over human-touch lead_activities.
        last_completed_followup: MAX(completed_at) over done follow-ups.
        business_tz: IANA zone string (e.g. "Asia/Kolkata") — drives the
            local-day boundary for OVERDUE / DUE_TODAY classification.
        now_utc: Aware UTC "now". Passed in (not computed inline) so the
            same wall-clock instant classifies every lead in a batch and
            so tests can pin time."""
    # Terminal stage short-circuits the cascade. Closed/won/lost enquiries
    # never get an action label even when stale pending follow-ups exist
    # (that's a separate data-hygiene concern, not a "what should I do"
    # signal). The spec's literal first-match order would let an overdue
    # followup nag on a Won deal — undesirable in practice.
    if _is_terminal_stage(lead.stage_name):
        return NextActionSummary(
            type=NextActionType.NONE,
            label="",
            urgency_rank=urgency_rank_for(NextActionType.NONE),
            relevant_date=None,
        )

    today_local: date = today_in(business_tz)

    # ---- Cascade in declared order, with the NO_FOLLOWUP_SET / GONE_QUIET
    # exclusivity that makes both reachable under first-match. ----

    if next_pending_followup is not None:
        scheduled_at = _as_utc(next_pending_followup.scheduled_at)
        scheduled_local_date = scheduled_at.astimezone(_zone(business_tz)).date()

        if scheduled_local_date < today_local:
            days_late = (today_local - scheduled_local_date).days
            label = (
                "Follow up — 1 day late"
                if days_late == 1
                else f"Follow up — {days_late} days late"
            )
            return NextActionSummary(
                type=NextActionType.FOLLOWUP_OVERDUE,
                label=label,
                urgency_rank=urgency_rank_for(NextActionType.FOLLOWUP_OVERDUE),
                relevant_date=scheduled_at,
            )

        if scheduled_local_date == today_local:
            return NextActionSummary(
                type=NextActionType.FOLLOWUP_DUE_TODAY,
                label="Follow up today",
                urgency_rank=urgency_rank_for(NextActionType.FOLLOWUP_DUE_TODAY),
                relevant_date=scheduled_at,
            )

        return NextActionSummary(
            type=NextActionType.FOLLOWUP_UPCOMING,
            label=f"Follow up on {_format_short_date(scheduled_at, business_tz)}",
            urgency_rank=urgency_rank_for(NextActionType.FOLLOWUP_UPCOMING),
            relevant_date=scheduled_at,
        )

    # No pending follow-up, lead is open. Inferred nudges only fire on
    # early-funnel stages (locked: see EARLY_FUNNEL_STAGE_NAMES). For a
    # mid- or late-funnel lead without a follow-up we return NONE — the
    # assumption being that the owner has consciously moved past active
    # outreach (e.g. quote sent, work in progress) and an inferred nudge
    # would be noise. Explicit follow-up types (OVERDUE / DUE_TODAY) are
    # NOT gated by this — they returned already if applicable.
    if not _is_early_funnel_stage(lead.stage_name):
        return NextActionSummary(
            type=NextActionType.NONE,
            label="",
            urgency_rank=urgency_rank_for(NextActionType.NONE),
            relevant_date=None,
        )

    # Cut between NO_FOLLOWUP_SET and GONE_QUIET on the latest human signal.
    lead_created_at = _as_utc(lead.created_at)
    candidates = [
        ts for ts in (
            _as_utc(last_human_touch),
            _as_utc(last_completed_followup),
            lead_created_at,
        )
        if ts is not None
    ]
    # `candidates` is non-empty in practice (every lead has created_at).
    # Defensive fallback: treat a lead with no signal as fresh.
    last_signal = max(candidates) if candidates else now_utc
    days_silent = (now_utc - last_signal).days

    if days_silent >= GONE_QUIET_DAYS:
        return NextActionSummary(
            type=NextActionType.GONE_QUIET,
            label=f"Quiet {days_silent} days — nudge?",
            urgency_rank=urgency_rank_for(NextActionType.GONE_QUIET),
            relevant_date=last_signal,
        )

    return NextActionSummary(
        type=NextActionType.NO_FOLLOWUP_SET,
        label="Set a follow-up",
        urgency_rank=urgency_rank_for(NextActionType.NO_FOLLOWUP_SET),
        # Lead creation date is the "how long has this been sitting"
        # signal for cross-list ranking within NO_FOLLOWUP_SET.
        relevant_date=lead_created_at,
    )


def compute_next_actions_bulk(
    *,
    leads: Iterable[LeadRead],
    pending_followups: list[LeadFollowup],
    last_human_touch_by_lead: dict[UUID, datetime],
    last_completed_followup_by_lead: dict[UUID, datetime],
    business_tz: str,
    now_utc: Optional[datetime] = None,
) -> dict[UUID, NextActionSummary]:
    """Compute next_action for every lead in a single pass.

    The three input dicts come from the bulk repository helpers in
    `lead_repository` / `lead_followup_repository`. `pending_followups` is
    ordered by scheduled_at ASC — we pick the first occurrence per lead
    as the "next" one.

    Returns a `{lead_id: NextActionSummary}` dict. Leads not in the input
    are simply absent from the result (caller should default to None or
    re-compute)."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    next_followup_by_lead: dict[UUID, LeadFollowup] = {}
    for followup in pending_followups:
        # First-wins on the ASC-sorted list = earliest pending follow-up.
        next_followup_by_lead.setdefault(followup.lead_id, followup)

    out: dict[UUID, NextActionSummary] = {}
    for lead in leads:
        out[lead.id] = compute_next_action(
            lead=lead,
            next_pending_followup=next_followup_by_lead.get(lead.id),
            last_human_touch=last_human_touch_by_lead.get(lead.id),
            last_completed_followup=last_completed_followup_by_lead.get(lead.id),
            business_tz=business_tz,
            now_utc=now_utc,
        )
    return out


def sort_key_for_home(summary: NextActionSummary) -> tuple:
    """Sort key for the dashboard "leads needing action" list.

    Primary: `urgency_rank` ASC (cascade priority).
    Secondary: `relevant_date` ASC NULLS LAST — for OVERDUE that's "most
    overdue first"; for GONE_QUIET it's "quietest first"; for
    NO_FOLLOWUP_SET it's "oldest sitting first".

    Cross-list tie-break beyond this is parked — revisit when real data
    shows whether estimated_value or days-languishing produces a better
    ordering. See `Docs/plans/next-action-cascade.md` §parked."""
    # `datetime.max` as the NULLS-LAST sentinel. Naive on purpose — we
    # only compare against itself when relevant_date is None, and the
    # primary `urgency_rank` will have already separated rows that
    # legitimately have dates.
    sentinel = datetime.max.replace(tzinfo=timezone.utc)
    rd = summary.relevant_date
    if rd is not None and rd.tzinfo is None:
        rd = rd.replace(tzinfo=timezone.utc)
    return (summary.urgency_rank, rd or sentinel)


def is_home_action(summary: NextActionSummary) -> bool:
    """True when this action belongs on the dashboard's "needs attention"
    list (cascade types 1-4). UPCOMING and NONE are filtered out."""
    return summary.type in HOME_NEEDS_ACTION_TYPES
