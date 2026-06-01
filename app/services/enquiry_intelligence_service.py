"""Per-enquiry computed intelligence.

Three outputs are kept up to date per enquiry:
  1. requirement_summary — 1–2 sentence factual paraphrase of what the
     customer asked for. Used for reading.
  2. demand_tags[]       — normalised lowercase noun phrases stored
     per-business in the `demand_tags` table and linked to the enquiry
     via `enquiry_demand_tags`. Used for analytics ("how many enquiries
     mentioned modular kitchen this quarter?").
  3. activity_summary    — rolling factual summary of the activity log.
     Static text — ABSOLUTE dates only, never relative ("9 days ago" is
     forbidden, because the summary is stored and read days later).

All three mutations are async and meant to be enqueued via FastAPI
BackgroundTasks (no Celery). The hot request path stays untouched: routes
finish responding, then a background coroutine picks up.

Triggers (wired from `lead_service` / `lead_activity_service`):

    enquiry created                        -> compute_requirement
                                            + rebuild_activity_summary
    enquiry.requirement OR notes edited    -> compute_requirement
    activity log row CREATED               -> update_activity_summary
                                            (incremental, single new row)
    activity log row EDITED or DELETED     -> rebuild_activity_summary
                                            (full re-read of the diary)

No catalog coupling here — demand_tags are free-form noun phrases, not
catalog item ids.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from datetime import datetime
from typing import Any, Iterable
from uuid import UUID

from sqlmodel import Session, select

from app.config.llm_config import llm_settings
from app.core.database import engine
from app.core.time_utils import format_local
from app.models.enums import DemandTagOrigin, LeadActivityType
from app.models.lead import Lead, LeadActivity
from app.repositories.business_repository import get_business_by_id
from app.repositories.demand_tag_repository import (
    get_or_create_demand_tag,
    replace_enquiry_demand_tags,
)
from app.services.enquiry_intelligence_prompts import (
    ACTIVITY_INCREMENTAL_SYSTEM_PROMPT,
    ACTIVITY_INCREMENTAL_USER_TEMPLATE,
    ACTIVITY_REBUILD_SYSTEM_PROMPT,
    ACTIVITY_REBUILD_USER_TEMPLATE,
    REQUIREMENT_SYSTEM_PROMPT,
    REQUIREMENT_USER_TEMPLATE,
)

logger = logging.getLogger(__name__)

try:
    from litellm import acompletion
except ImportError:  # pragma: no cover - validated at runtime
    acompletion = None


# Scope-changing activity types. NOTE entries are the canonical owner-
# authored requirement clarifications; LEAD_UPDATED rows surface field
# edits (title / notes / estimated_value) the owner made on the enquiry.
# Everything else (calls, payments, follow-up bookkeeping, status changes)
# is part of the activity log but not part of "what the customer wants".
_SCOPE_CHANGING_ACTIVITY_TYPES: frozenset[LeadActivityType] = frozenset({
    LeadActivityType.NOTE,
    LeadActivityType.LEAD_UPDATED,
})


_WHITESPACE_RE = re.compile(r"\s+")


def normalise_demand_name(raw: str) -> str:
    """Canonical form for demand_tag.name. Lowercase, trimmed, internal
    whitespace collapsed. The DB UNIQUE(business_id, name) constraint
    assumes this normalisation has already been applied."""
    return _WHITESPACE_RE.sub(" ", raw.strip().lower())


# ---------------------------------------------------------------------------
# LLM plumbing
# ---------------------------------------------------------------------------

def _resolve_api_key(model: str) -> str | None:
    """Mirror of LLMService._resolve_api_key — kept local so this module
    doesn't depend on the chat-side LLMService wrapper."""
    name = model.lower()
    if name.startswith("gemini/"):
        return (
            llm_settings.google_api_key
            or llm_settings.llm_api_key
            or os.getenv("GOOGLE_API_KEY")
        )
    if name.startswith("anthropic/"):
        return (
            llm_settings.anthropic_api_key
            or llm_settings.llm_api_key
            or os.getenv("ANTHROPIC_API_KEY")
        )
    if name.startswith("openai/") or name.startswith("gpt"):
        return os.getenv("OPENAI_API_KEY") or llm_settings.llm_api_key
    return llm_settings.llm_api_key


async def _llm_call(
    *,
    system_prompt: str,
    user_content: str,
    json_mode: bool = False,
) -> str:
    """Single-shot LLM call (no tools, no loop). Returns the assistant's
    text content as-is."""
    if acompletion is None:
        raise RuntimeError(
            "LiteLLM is not installed — cannot compute enquiry intelligence."
        )

    model = llm_settings.summary_model
    max_tokens = llm_settings.summary_max_output_tokens

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "api_key": _resolve_api_key(model),
        "max_tokens": max_tokens,
        "temperature": llm_settings.temperature,
        "timeout": llm_settings.timeout,
        "drop_params": True,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = await acompletion(**kwargs)
    choice = response.choices[0]
    content = getattr(choice.message, "content", "") or ""
    if isinstance(content, list):  # some providers return content as parts
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return content.strip()


# ---------------------------------------------------------------------------
# Input rendering — keep what gets sent to the LLM honest and minimal
# ---------------------------------------------------------------------------

def _render_activity_line(activity: LeadActivity, tz: str) -> str:
    """Single activity entry as one line: `[<absolute date>] <type>: <desc>`.

    Absolute-date-only is the project's hard rule for stored summaries.
    Using format_local("%d %b %Y") yields e.g. "12 May 2026" — stable
    forever, no relative-time drift."""
    when = format_local(activity.created_at, tz=tz)
    type_label = activity.type.value if hasattr(activity.type, "value") else str(activity.type)
    description = (activity.description or "").strip() or "(no description)"
    return f"[{when}] {type_label}: {description}"


def _build_requirement_user_content(
    *,
    lead: Lead,
    scope_changing_activities: Iterable[LeadActivity],
    tz: str,
) -> str:
    """Render the user-role content for compute_requirement.

    Project note: the Lead model has no separate `requirement_text`
    column — `lead.title` is the enquiry headline that owners write when
    capturing the customer's ask, so it stands in for the requirement
    here. Budget (estimated_value) and source are deliberately omitted —
    those are structured columns and the prompt forbids surfacing them.
    """
    requirement_text = (lead.title or "").strip() or "(none)"
    notes_text = (lead.notes or "").strip() or "(none)"
    activities = list(scope_changing_activities)
    if activities:
        scope_lines = "\n".join(
            _render_activity_line(a, tz) for a in activities
        )
    else:
        scope_lines = "(none)"
    return REQUIREMENT_USER_TEMPLATE.format(
        requirement_text=requirement_text,
        notes=notes_text,
        scope_activity_lines=scope_lines,
    )


# ---------------------------------------------------------------------------
# DB access helpers
# ---------------------------------------------------------------------------

def _business_timezone(session: Session, business_id: UUID) -> str:
    business = get_business_by_id(session, business_id)
    if business is None:
        return "Asia/Kolkata"
    return business.timezone or "Asia/Kolkata"


def _load_lead(session: Session, lead_id: UUID) -> Lead | None:
    return session.exec(select(Lead).where(Lead.id == lead_id)).first()


def _load_activity(
    session: Session, activity_id: UUID,
) -> LeadActivity | None:
    return session.exec(
        select(LeadActivity).where(LeadActivity.id == activity_id),
    ).first()


def _load_all_activities(
    session: Session, lead_id: UUID,
) -> list[LeadActivity]:
    stmt = (
        select(LeadActivity)
        .where(LeadActivity.lead_id == lead_id)
        .order_by(LeadActivity.created_at.asc())
    )
    return list(session.exec(stmt).all())


def _load_scope_changing_activities(
    session: Session, lead_id: UUID,
) -> list[LeadActivity]:
    types = [t.value for t in _SCOPE_CHANGING_ACTIVITY_TYPES]
    stmt = (
        select(LeadActivity)
        .where(
            LeadActivity.lead_id == lead_id,
            LeadActivity.type.in_(types),
        )
        .order_by(LeadActivity.created_at.asc())
    )
    return list(session.exec(stmt).all())


# ---------------------------------------------------------------------------
# Public service surface
# ---------------------------------------------------------------------------

async def compute_requirement(lead_id: UUID) -> None:
    """Recompute `requirement_summary` and `demand_tags` for one enquiry.

    Opens its own DB session (background work, no inherited request
    session). Idempotent: replays produce the same summary text + the same
    set of (lowercased) tag links."""
    with Session(engine) as session:
        lead = _load_lead(session, lead_id)
        if lead is None:
            logger.warning(
                "compute_requirement skipped: lead %s not found", lead_id,
            )
            return

        tz = _business_timezone(session, lead.business_id)
        scope_changes = _load_scope_changing_activities(session, lead_id)
        user_content = _build_requirement_user_content(
            lead=lead, scope_changing_activities=scope_changes, tz=tz,
        )

        try:
            raw = await _llm_call(
                system_prompt=REQUIREMENT_SYSTEM_PROMPT,
                user_content=user_content,
                json_mode=True,
            )
        except Exception as exc:  # noqa: BLE001 — background work, swallow
            logger.exception(
                "compute_requirement LLM call failed for lead %s: %s",
                lead_id, exc,
            )
            return

        parsed = _parse_requirement_json(raw)
        if parsed is None:
            logger.warning(
                "compute_requirement got unparseable LLM output for "
                "lead %s: %r", lead_id, raw[:200],
            )
            return
        requirement_summary, demand_phrases = parsed

        # --- persist summary ---
        lead.requirement_summary = requirement_summary or None
        lead.summary_updated_at = datetime.utcnow()
        session.add(lead)

        # --- persist demand_tags ---
        tag_ids: list[UUID] = []
        for raw_phrase in demand_phrases:
            if not isinstance(raw_phrase, str):
                continue
            norm = normalise_demand_name(raw_phrase)
            if not norm:
                continue
            tag = get_or_create_demand_tag(
                session,
                business_id=lead.business_id,
                name=norm,
                origin=DemandTagOrigin.AI,
            )
            tag_ids.append(tag.id)

        replace_enquiry_demand_tags(
            session, enquiry_id=lead_id, demand_tag_ids=tag_ids,
        )
        session.commit()


def _parse_requirement_json(raw: str) -> tuple[str, list[str]] | None:
    """Tolerant JSON extraction. Strips ```json fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        # Drop leading fence (```json or ```) and trailing fence.
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    summary = payload.get("requirement_summary")
    demand = payload.get("demand")
    if not isinstance(summary, str):
        summary = ""
    if not isinstance(demand, list):
        demand = []
    return summary.strip(), demand


async def update_activity_summary(
    lead_id: UUID,
    new_activity_id: UUID,
) -> None:
    """Incremental fold of ONE new activity into the rolling summary.

    Skipped (no-op) when the activity is already the high-water mark — the
    same row will not be folded twice. This is the main path: every newly
    created activity hits it, regardless of activity type."""
    with Session(engine) as session:
        lead = _load_lead(session, lead_id)
        if lead is None:
            logger.warning(
                "update_activity_summary skipped: lead %s not found",
                lead_id,
            )
            return

        # Skip if this row is already the watermark — folding it again
        # would be a no-op at best and could cause double-counting at
        # worst. The spec calls this out explicitly.
        if lead.last_activity_id_summarized == new_activity_id:
            return

        activity = _load_activity(session, new_activity_id)
        if activity is None or activity.lead_id != lead_id:
            logger.warning(
                "update_activity_summary skipped: activity %s not found "
                "or not on lead %s", new_activity_id, lead_id,
            )
            return

        tz = _business_timezone(session, lead.business_id)
        max_tokens = llm_settings.summary_max_output_tokens
        current = (lead.activity_summary or "").strip() or "(none)"
        activity_date = format_local(activity.created_at, tz=tz)
        activity_type = (
            activity.type.value
            if hasattr(activity.type, "value")
            else str(activity.type)
        )
        activity_desc = (activity.description or "").strip() or "(no description)"
        activity_text = f"{activity_type}: {activity_desc}"

        user_content = ACTIVITY_INCREMENTAL_USER_TEMPLATE.format(
            current_summary=current,
            activity_date=activity_date,
            activity_text=activity_text,
        )

        try:
            updated = await _llm_call(
                system_prompt=ACTIVITY_INCREMENTAL_SYSTEM_PROMPT.format(
                    max_tokens=max_tokens,
                ),
                user_content=user_content,
                json_mode=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "update_activity_summary LLM call failed for lead %s: %s",
                lead_id, exc,
            )
            return

        if not updated:
            return

        lead.activity_summary = updated
        lead.last_activity_id_summarized = activity.id
        lead.summary_updated_at = datetime.utcnow()
        session.add(lead)
        session.commit()


async def rebuild_activity_summary(lead_id: UUID) -> None:
    """Full re-read path: rewrite `activity_summary` from every activity
    entry on the lead. Triggered on activity EDIT or DELETE (the
    incremental path can't undo or revise; only a fresh pass can)."""
    with Session(engine) as session:
        lead = _load_lead(session, lead_id)
        if lead is None:
            logger.warning(
                "rebuild_activity_summary skipped: lead %s not found",
                lead_id,
            )
            return

        activities = _load_all_activities(session, lead_id)
        if not activities:
            lead.activity_summary = None
            lead.last_activity_id_summarized = None
            lead.summary_updated_at = datetime.utcnow()
            session.add(lead)
            session.commit()
            return

        tz = _business_timezone(session, lead.business_id)
        max_tokens = llm_settings.summary_max_output_tokens
        rendered = "\n".join(_render_activity_line(a, tz) for a in activities)
        user_content = ACTIVITY_REBUILD_USER_TEMPLATE.format(
            activity_lines=rendered,
        )

        try:
            summary = await _llm_call(
                system_prompt=ACTIVITY_REBUILD_SYSTEM_PROMPT.format(
                    max_tokens=max_tokens,
                ),
                user_content=user_content,
                json_mode=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "rebuild_activity_summary LLM call failed for lead %s: %s",
                lead_id, exc,
            )
            return

        latest = activities[-1]
        lead.activity_summary = summary or None
        lead.last_activity_id_summarized = latest.id
        lead.summary_updated_at = datetime.utcnow()
        session.add(lead)
        session.commit()


# ---------------------------------------------------------------------------
# BackgroundTasks adapters — FastAPI's BackgroundTasks expects callables
# it can invoke synchronously. These tiny shims wrap the coroutines so the
# trigger sites in lead_service / lead_activity_service can just call
# `background_tasks.add_task(enqueue_*)` and move on.
# ---------------------------------------------------------------------------

def _run(coro: Any) -> None:
    """Drive a coroutine to completion from a sync context. Background
    tasks run after the response is sent, so a fresh event loop here is
    fine — we are not inside the request loop."""
    asyncio.run(coro)


def enqueue_compute_requirement(lead_id: UUID) -> None:
    _run(compute_requirement(lead_id))


def enqueue_update_activity_summary(
    lead_id: UUID, new_activity_id: UUID,
) -> None:
    _run(update_activity_summary(lead_id, new_activity_id))


def enqueue_rebuild_activity_summary(lead_id: UUID) -> None:
    _run(rebuild_activity_summary(lead_id))


def enqueue_on_enquiry_created(lead_id: UUID) -> None:
    """Convenience trigger for the enquiry-created path: compute the
    requirement AND seed the activity summary (the LEAD_CREATED row is
    already in the diary)."""
    _run(_on_enquiry_created(lead_id))


async def _on_enquiry_created(lead_id: UUID) -> None:
    # Run sequentially — keeps DB contention obvious and avoids ordering
    # surprises if the same lead row is touched by both calls.
    await compute_requirement(lead_id)
    await rebuild_activity_summary(lead_id)


# ---------------------------------------------------------------------------
# Fire-and-forget thread shim for the lead_activity repository chokepoint.
#
# Every lead_activity row that lands hits this path so the incremental
# activity_summary update runs for ALL activity types — note/call/whatsapp/
# meeting AND system-emitted rows (status_change / followup_* / invoice_* /
# payment_* / lead_created / lead_updated). Retrofitting BackgroundTasks
# into every route that emits a system activity would be invasive; this
# shim keeps the trigger surface on the single chokepoint that already
# owns the write.
#
# The watermark check inside `update_activity_summary` (skip if
# `last_activity_id_summarized == new_activity_id`) makes the chokepoint
# trigger safe to race against the route-level rebuild on create:
# whichever wins last leaves the lead in a consistent state.
# ---------------------------------------------------------------------------

def fire_update_activity_summary(
    lead_id: UUID, new_activity_id: UUID,
) -> None:
    """Spawn a daemon thread that drives the incremental update. Returns
    immediately. Errors inside the thread are logged, never propagated."""
    def _runner() -> None:
        try:
            asyncio.run(update_activity_summary(lead_id, new_activity_id))
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "fire_update_activity_summary failed for lead %s "
                "activity %s: %s", lead_id, new_activity_id, exc,
            )

    threading.Thread(target=_runner, daemon=True).start()


def fire_rebuild_activity_summary(lead_id: UUID) -> None:
    """Spawn a daemon thread that does a full rebuild. Use this from
    multi-write flows (e.g. resolve_followup, which creates 1–2 activities
    in one transaction) where the incremental path's "fold ONE row" model
    would miss some of them. Same fire-and-forget semantics as
    `fire_update_activity_summary`."""
    def _runner() -> None:
        try:
            asyncio.run(rebuild_activity_summary(lead_id))
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "fire_rebuild_activity_summary failed for lead %s: %s",
                lead_id, exc,
            )

    threading.Thread(target=_runner, daemon=True).start()
