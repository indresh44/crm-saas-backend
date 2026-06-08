from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.customer import Customer
from app.models.enums import LeadActivityType
from app.models.lead import Lead, LeadActivity, LeadRead
from app.models.lead_followup import LeadFollowup
from app.models.pipeline import Pipeline, PipelineStage
from app.repositories.demand_tag_repository import get_demand_tags_by_lead_ids


# Activity types that count as a "human touch" for the GONE_QUIET cascade
# rule — call/WhatsApp/meeting/note (the user's spec). These types are
# owner-logged by design: every emit path goes through
# `lead_activity_service.create_activity()` which requires a current_user.
# System-generated events (STATUS_CHANGE, FOLLOWUP_*, INVOICE_*,
# PAYMENT_*, LEAD_CREATED, LEAD_UPDATED) use different types and are
# already excluded by this filter alone — no actor_type check needed.
#
# Earlier impl also filtered on `actor_type IN (HUMAN, AI)`. That was
# over-defensive and caused a real bug: pre-0042 rows have NULL
# actor_type, so `IN (...)` (which is NULL-rejecting in SQL) silently
# dropped them — making a recently-touched legacy lead look quiet and
# wrongly firing GONE_QUIET. Removed.
_HUMAN_TOUCH_ACTIVITY_TYPES = (
    LeadActivityType.CALL.value,
    LeadActivityType.WHATSAPP.value,
    LeadActivityType.MEETING.value,
    LeadActivityType.NOTE.value,
)


def create_lead(session: Session, lead: Lead) -> Lead:
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead


def update_lead(session: Session, lead: Lead) -> Lead:
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead


def get_lead_by_id(session: Session, business_id: UUID, lead_id: UUID) -> Lead | None:
    statement = select(Lead).where(Lead.id == lead_id, Lead.business_id == business_id)
    return session.exec(statement).first()


def get_lead_by_phone_normalized(
    session: Session,
    business_id: UUID,
    phone_normalized: str,
) -> Lead | None:
    """Most recent lead for a (business, normalized phone) pair, joined via customer.
    Used by the WhatsApp inbound ingest to attach incoming messages to a lead."""
    statement = (
        select(Lead)
        .join(Customer, Lead.customer_id == Customer.id)
        .where(
            Lead.business_id == business_id,
            Customer.phone_normalized == phone_normalized,
        )
        .order_by(Lead.created_at.desc())
    )
    return session.exec(statement).first()


def list_leads_for_business(
    session: Session,
    business_id: UUID,
    customer_id: UUID | None = None,
) -> list[LeadRead]:
    statement = (
        select(
            Lead,
            Customer.name,
            Customer.phone,
            PipelineStage.name,
            PipelineStage.color,
        )
        .outerjoin(Customer, Lead.customer_id == Customer.id)
        .outerjoin(PipelineStage, Lead.stage_id == PipelineStage.id)
        .where(Lead.business_id == business_id)
        .order_by(Lead.created_at.desc())
    )

    if customer_id is not None:
        statement = statement.where(Lead.customer_id == customer_id)

    rows = session.exec(statement).all()
    results: list[LeadRead] = []
    for lead, customer_name, customer_phone, stage_name, stage_color in rows:
        lead_read = LeadRead.model_validate(lead, from_attributes=True)
        lead_read.customer_name = customer_name
        lead_read.customer_phone = customer_phone
        lead_read.stage_name = stage_name
        lead_read.stage_color = stage_color
        results.append(lead_read)

    # 0045 — one bulk SELECT for demand_tags across the whole page. Cheap
    # join, stable ordering by tag name. Attached after the main loop so
    # `model_validate` (which doesn't see the M2M) doesn't drop them.
    tags_by_lead = get_demand_tags_by_lead_ids(
        session, [r.id for r in results],
    )
    for lead_read in results:
        lead_read.demand_tags = tags_by_lead.get(lead_read.id, [])

    return results


def move_lead_stage(session: Session, lead: Lead, new_stage_id: UUID) -> Lead:
    lead.stage_id = new_stage_id
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead


# ---------------------------------------------------------------------------
# No-commit lead mutators used by `resolve_followup`. Same atomicity
# rationale as the followup repo helpers — caller owns the commit.
# ---------------------------------------------------------------------------


def touch_contacted(session: Session, lead: Lead) -> Lead:
    """Bump last_contacted_at to now. Used by positive/neutral outcomes
    where the owner actually spoke to (or got a reply from) the lead.
    Reads as the GONE_QUIET cascade's freshness anchor going forward."""
    lead.last_contacted_at = datetime.now(timezone.utc)
    session.add(lead)
    session.flush()
    return lead


def flag_phone(session: Session, lead: Lead) -> Lead:
    """Set phone_flagged=True. Used on wrong_number outcomes so the UI
    can render a "verify number" warning the next time the lead opens."""
    lead.phone_flagged = True
    session.add(lead)
    session.flush()
    return lead


def set_stage(session: Session, lead: Lead, new_stage_id: UUID) -> Lead:
    """No-commit equivalent of move_lead_stage. The caller emits the
    matching status_change activity row in the same transaction."""
    lead.stage_id = new_stage_id
    session.add(lead)
    session.flush()
    return lead


def get_pipeline_stage_by_id(
    session: Session,
    business_id: UUID,
    stage_id: UUID,
) -> PipelineStage | None:
    statement = (
        select(PipelineStage)
        .join(Pipeline, PipelineStage.pipeline_id == Pipeline.id)
        .where(PipelineStage.id == stage_id, Pipeline.business_id == business_id)
    )
    return session.exec(statement).first()


def create_lead_activity(session: Session, activity: LeadActivity) -> LeadActivity:
    """The ONE chokepoint that mints LeadActivity rows.

    Stamps the diary fields (actor_type, chat_session_id, task_id) from the
    ambient `ActorContext` so callers don't have to thread them through every
    service signature. If a caller has already set these fields on the
    activity object (rare — only for tests or migrations that set explicit
    values), the caller's values win.

    Note for new emit sites added in batches after 0042: always set `payload`
    on the activity object yourself before calling this helper — the
    chokepoint does NOT synthesise payload (it can't; only the caller knows
    the event's facts). The source-grep test in
    `tests/test_lead_activity_emits_pg.py` enforces that every emit site for
    a SYSTEM-typed event sets payload."""
    # Local import to avoid a circular import at module load time
    # (actor_context imports nothing from app.models, but lead_repository is
    # imported very early in service modules; deferring keeps the import
    # graph clean).
    from app.core.actor_context import current_actor, warn_if_default_on_write

    warn_if_default_on_write()
    ctx = current_actor()

    if activity.actor_type is None:
        activity.actor_type = ctx.actor_type
    if activity.chat_session_id is None:
        activity.chat_session_id = ctx.chat_session_id
    if activity.task_id is None:
        activity.task_id = ctx.task_id

    session.add(activity)
    session.commit()
    session.refresh(activity)

    # 0045 — every committed activity row feeds the incremental
    # activity_summary path. Spawned as a daemon thread (fire-and-forget)
    # so the chokepoint can serve callers that don't carry a
    # FastAPI BackgroundTasks (e.g. the WhatsApp webhook, scheduled jobs,
    # follow-up resolution). The watermark check inside the service makes
    # this idempotent and safe to race against route-level rebuilds.
    try:
        from app.services.enquiry_intelligence_service import (
            fire_update_activity_summary,
        )

        fire_update_activity_summary(activity.lead_id, activity.id)
    except Exception:  # noqa: BLE001 — best-effort, never break the write
        # Import-time failure or daemon-spawn failure must not poison the
        # primary commit. The intelligence layer is advisory data.
        pass

    return activity


def get_pipeline_by_id_for_business(
    session: Session,
    business_id: UUID,
    pipeline_id: UUID,
) -> Pipeline | None:
    statement = select(Pipeline).where(Pipeline.id == pipeline_id, Pipeline.business_id == business_id)
    return session.exec(statement).first()


def list_stages_for_pipeline(session: Session, pipeline_id: UUID) -> list[PipelineStage]:
    statement = (
        select(PipelineStage)
        .where(PipelineStage.pipeline_id == pipeline_id)
        .order_by(PipelineStage.position)
    )
    return list(session.exec(statement).all())


def get_lead_read_by_id(
    session: Session,
    business_id: UUID,
    lead_id: UUID,
) -> LeadRead | None:
    """Single-lead enriched read — same joins as `list_leads_for_business`.

    The plain `get_lead_by_id` returns a bare `Lead` (no customer / stage
    enrichment); this helper exists so the GET /leads/{id} endpoint can
    populate `customer_name`, `stage_name`, etc. and (via the service
    layer) `next_action`."""
    statement = (
        select(
            Lead,
            Customer.name,
            Customer.phone,
            PipelineStage.name,
            PipelineStage.color,
        )
        .outerjoin(Customer, Lead.customer_id == Customer.id)
        .outerjoin(PipelineStage, Lead.stage_id == PipelineStage.id)
        .where(Lead.id == lead_id, Lead.business_id == business_id)
    )
    row = session.exec(statement).first()
    if row is None:
        return None
    lead, customer_name, customer_phone, stage_name, stage_color = row
    lead_read = LeadRead.model_validate(lead, from_attributes=True)
    lead_read.customer_name = customer_name
    lead_read.customer_phone = customer_phone
    lead_read.stage_name = stage_name
    lead_read.stage_color = stage_color
    # 0045 — attach demand_tags. Same shape as the list path.
    lead_read.demand_tags = get_demand_tags_by_lead_ids(
        session, [lead.id],
    ).get(lead.id, [])
    return lead_read


def get_last_human_touch_per_lead(
    session: Session,
    lead_ids: Sequence[UUID],
) -> dict[UUID, datetime]:
    """Bulk-fetch the latest human-touch timestamp per lead.

    Anchor for the GONE_QUIET cascade rule. Filters to CALL / WHATSAPP /
    MEETING / NOTE activity types — those are owner-logged by design and
    cover the "touch" definition in the locked spec (no actor_type filter:
    see `_HUMAN_TOUCH_ACTIVITY_TYPES` block comment). Single query,
    GROUP BY lead_id."""
    if not lead_ids:
        return {}
    statement = (
        select(LeadActivity.lead_id, func.max(LeadActivity.created_at))
        .where(
            LeadActivity.lead_id.in_(list(lead_ids)),
            LeadActivity.type.in_(_HUMAN_TOUCH_ACTIVITY_TYPES),
        )
        .group_by(LeadActivity.lead_id)
    )
    return {lead_id: ts for lead_id, ts in session.exec(statement).all()}


def list_recent_human_activities_for_lead(
    session: Session,
    lead_id: UUID,
    limit: int = 3,
) -> list[LeadActivity]:
    """Last N human-touch activities for one lead, newest first.

    Same filter as the GONE_QUIET cascade (CALL/WHATSAPP/MEETING/NOTE
    types) so the dashboard context strip and the cascade agree on
    "what counts as activity"."""
    statement = (
        select(LeadActivity)
        .where(
            LeadActivity.lead_id == lead_id,
            LeadActivity.type.in_(_HUMAN_TOUCH_ACTIVITY_TYPES),
        )
        .order_by(LeadActivity.created_at.desc())
        .limit(limit)
    )
    return list(session.exec(statement).all())


def get_last_completed_followup_per_lead(
    session: Session,
    lead_ids: Sequence[UUID],
) -> dict[UUID, datetime]:
    """Bulk-fetch the latest completed-follow-up timestamp per lead.

    Second anchor for the GONE_QUIET cascade — the owner may have done a
    follow-up without separately logging a CALL/WHATSAPP/MEETING/NOTE
    activity, so a recent completion still counts as "not quiet".
    Falls back to scheduled_at when completed_at is NULL on a done row
    (older rows from before completed_at became required)."""
    if not lead_ids:
        return {}
    statement = (
        select(
            LeadFollowup.lead_id,
            func.max(func.coalesce(LeadFollowup.completed_at, LeadFollowup.scheduled_at)),
        )
        .where(
            LeadFollowup.lead_id.in_(list(lead_ids)),
            LeadFollowup.status == "done",
        )
        .group_by(LeadFollowup.lead_id)
    )
    return {lead_id: ts for lead_id, ts in session.exec(statement).all()}


def list_leads_for_business_and_stage_ids(
    session: Session,
    business_id: UUID,
    stage_ids: Sequence[UUID],
) -> list[Lead]:
    if not stage_ids:
        return []

    statement = select(Lead).where(
        Lead.business_id == business_id,
        Lead.stage_id.in_(list(stage_ids)),
    )
    return list(session.exec(statement).all())
