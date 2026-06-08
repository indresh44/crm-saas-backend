from datetime import date, datetime, time, timedelta, timezone
from typing import Any, List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.core.json_safe import safe_jsonify
from app.core.time_utils import day_bounds_utc, format_local, today_in
from app.models.enums import LeadActivityType, Outcome, ResultAction
from app.models.lead import Lead, LeadActivity
from app.models.lead_followup import (
    LeadFollowup,
    LeadFollowupCancel,
    LeadFollowupCreate,
    LeadFollowupDone,
    LeadFollowupReschedule,
    LeadFollowupTodayRead,
    LeadFollowupUpdate,
)
from app.models.user import User
from app.repositories import lead_activity_repository as activity_repo
from app.repositories import lead_followup_repository as followup_repo
from app.repositories import lead_repository as lead_repo
from app.repositories.business_repository import get_business_by_id
from app.repositories.lead_followup_repository import (
    create_lead_followup as repo_create_lead_followup,
    get_lead_followup_by_id,
    list_followups_before_datetime,
    list_followups_for_datetime_range,
    list_followups_for_lead,
    list_followups_with_filters,
    update_lead_followup as repo_update_lead_followup,
)
from app.repositories.lead_repository import create_lead_activity, get_lead_by_id
from app.repositories.pipeline_repository import get_stage_by_name_for_business
from app.services.enquiry_intelligence_service import (
    fire_rebuild_activity_summary,
)


def _business_timezone(session: Session, business_id: UUID) -> str:
    """Fetch the business's configured IANA timezone. Falls back to the helper default if absent."""
    business = get_business_by_id(session, business_id)
    if business is None:
        return "Asia/Kolkata"
    return business.timezone or "Asia/Kolkata"


def _get_lead_for_business(
    session: Session,
    business_id: UUID,
    lead_id: UUID,
) -> Lead:
    lead = get_lead_by_id(
        session=session,
        business_id=business_id,
        lead_id=lead_id,
    )
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )
    return lead


def _attach_lead_titles(
    session: Session,
    business_id: UUID,
    followups: list[LeadFollowup],
) -> list[LeadFollowupTodayRead]:
    lead_ids = {followup.lead_id for followup in followups}
    if not lead_ids:
        return []

    statement = select(Lead.id, Lead.title).where(
        Lead.business_id == business_id,
        Lead.id.in_(lead_ids),
    )
    lead_title_by_id = {
        lead_id: lead_title
        for lead_id, lead_title in session.exec(statement).all()
    }

    business_followups: list[LeadFollowupTodayRead] = []
    for followup in followups:
        lead_title = lead_title_by_id.get(followup.lead_id)
        if lead_title is None:
            continue
        business_followups.append(
            LeadFollowupTodayRead(
                id=followup.id,
                lead_id=followup.lead_id,
                scheduled_at=followup.scheduled_at,
                note=followup.note,
                status=followup.status,
                created_by=followup.created_by,
                created_at=followup.created_at,
                completed_at=followup.completed_at,
                lead_title=lead_title,
            )
        )
    return business_followups


def create_followup(
    session: Session,
    current_user: User,
    data: LeadFollowupCreate,
) -> LeadFollowup:
    lead = _get_lead_for_business(session, current_user.business_id, data.lead_id)
    followup = LeadFollowup(
        lead_id=lead.id,
        scheduled_at=data.scheduled_at,
        note=data.note,
        created_by=current_user.id,
    )
    result = repo_create_lead_followup(session, followup)
    tz = _business_timezone(session, current_user.business_id)
    create_lead_activity(session, LeadActivity(
        lead_id=lead.id,
        type=LeadActivityType.FOLLOWUP_SCHEDULED,
        description=f"Follow-up scheduled for {format_local(result.scheduled_at, tz)}"
                   + (f" — {result.note}" if result.note else ""),
        created_by=current_user.id,
        payload=safe_jsonify({
            "followup_id": result.id,
            "scheduled_at": result.scheduled_at,
            "note": result.note,
        }),
    ))
    return result


def list_followups(
    session: Session,
    current_user: User,
    lead_id: UUID | None = None,
    *,
    customer_id: UUID | None = None,
    status: str | None = None,
    date_value: date | None = None,
    before_date: date | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    followup_ids: list[UUID] | None = None,
    older_than_days: int | None = None,
    limit: int = 50,
) -> List[LeadFollowup]:
    if lead_id is not None and not any(
        value is not None
        for value in [customer_id, status, date_value, before_date, from_date, to_date, followup_ids, older_than_days]
    ):
        lead = _get_lead_for_business(session, current_user.business_id, lead_id)
        return list_followups_for_lead(session, lead_id=lead.id)

    if lead_id is not None:
        _get_lead_for_business(session, current_user.business_id, lead_id)

    return list_followups_with_filters(
        session=session,
        business_id=current_user.business_id,
        lead_id=lead_id,
        customer_id=customer_id,
        followup_ids=followup_ids,
        status=status,
        date_value=date_value,
        before_date=before_date,
        from_date=from_date,
        to_date=to_date,
        older_than_days=older_than_days,
        limit=limit,
    )


def get_followup(
    session: Session,
    current_user: User,
    followup_id: UUID,
) -> LeadFollowup:
    followup = get_lead_followup_by_id(session, followup_id=followup_id)
    if followup is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Follow-up not found",
        )

    _get_lead_for_business(session, current_user.business_id, followup.lead_id)
    return followup


def list_todays_followups(
    session: Session,
    current_user: User,
) -> List[LeadFollowupTodayRead]:
    tz = _business_timezone(session, current_user.business_id)
    start_at, end_at = day_bounds_utc(today_in(tz), tz)

    followups = list_followups_for_datetime_range(
        session=session,
        start_at=start_at,
        end_at=end_at,
    )
    return _attach_lead_titles(session, current_user.business_id, followups)


def list_overdue_followups(
    session: Session,
    current_user: User,
) -> List[LeadFollowup]:
    now = datetime.now(timezone.utc)
    followups = list_followups_before_datetime(
        session=session,
        before_at=now,
    )
    business_lead_ids = {
        followup.lead_id
        for followup in followups
        if get_lead_by_id(session, current_user.business_id, followup.lead_id) is not None
    }
    return [followup for followup in followups if followup.lead_id in business_lead_ids]


def mark_followup_done(
    session: Session,
    current_user: User,
    followup_id: UUID,
    data: LeadFollowupDone,
) -> LeadFollowup:
    followup = get_followup(session, current_user, followup_id)
    followup.status = "done"
    followup.completed_at = datetime.now(timezone.utc)
    if data.note is not None:
        followup.note = data.note

    result = repo_update_lead_followup(session, followup)
    create_lead_activity(session, LeadActivity(
        lead_id=followup.lead_id,
        type=LeadActivityType.FOLLOWUP_COMPLETED,
        description="Follow-up marked as done" + (f" — {result.note}" if result.note else ""),
        created_by=current_user.id,
        payload=safe_jsonify({
            "followup_id": result.id,
            "note": result.note,
        }),
    ))
    return result


def update_followup(
    session: Session,
    current_user: User,
    followup_id: UUID,
    data: LeadFollowupUpdate,
) -> LeadFollowup:
    followup = get_followup(session, current_user, followup_id)
    old_status = followup.status
    old_scheduled_at = followup.scheduled_at

    if data.scheduled_at is not None:
        followup.scheduled_at = data.scheduled_at
    if data.note is not None:
        followup.note = data.note
    if data.status is not None:
        followup.status = data.status
        if data.status == "done" and data.completed_at is None and followup.completed_at is None:
            followup.completed_at = datetime.now(timezone.utc)
        elif data.status != "done" and data.completed_at is None:
            followup.completed_at = None
    if data.completed_at is not None:
        followup.completed_at = data.completed_at

    result = repo_update_lead_followup(session, followup)

    activity_type = None
    desc = ""
    activity_payload: dict[str, Any] | None = None
    if data.status == "done" and old_status != "done":
        activity_type = LeadActivityType.FOLLOWUP_COMPLETED
        desc = "Follow-up marked as done" + (f" — {result.note}" if result.note else "")
        activity_payload = {"followup_id": result.id, "note": result.note}
    elif data.status == "cancelled" and old_status != "cancelled":
        activity_type = LeadActivityType.FOLLOWUP_CANCELLED
        desc = "Follow-up cancelled" + (f" — {result.note}" if result.note else "")
        activity_payload = {"followup_id": result.id, "note": result.note}
    elif data.scheduled_at is not None and data.scheduled_at != old_scheduled_at:
        activity_type = LeadActivityType.FOLLOWUP_RESCHEDULED
        tz = _business_timezone(session, current_user.business_id)
        desc = f"Follow-up rescheduled to {format_local(result.scheduled_at, tz)}" + (f" — {result.note}" if result.note else "")
        activity_payload = {
            "followup_id": result.id,
            "old_scheduled_at": old_scheduled_at,
            "new_scheduled_at": result.scheduled_at,
        }

    if activity_type:
        create_lead_activity(session, LeadActivity(
            lead_id=followup.lead_id,
            type=activity_type,
            description=desc,
            created_by=current_user.id,
            payload=safe_jsonify(activity_payload) if activity_payload else None,
        ))

    return result


def reschedule_followup(
    session: Session,
    current_user: User,
    followup_id: UUID,
    data: LeadFollowupReschedule,
) -> LeadFollowup:
    followup = get_followup(session, current_user, followup_id)
    if followup.status in {"done", "cancelled"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot reschedule a {followup.status} follow-up.",
        )
    return update_followup(
        session=session,
        current_user=current_user,
        followup_id=followup_id,
        data=LeadFollowupUpdate(
            scheduled_at=data.scheduled_at,
            note=data.note,
        ),
    )


def cancel_followup(
    session: Session,
    current_user: User,
    followup_id: UUID,
    data: LeadFollowupCancel,
) -> LeadFollowup:
    followup = get_followup(session, current_user, followup_id)
    if followup.status in {"done", "cancelled"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Follow-up is already {followup.status}.",
        )
    return update_followup(
        session=session,
        current_user=current_user,
        followup_id=followup_id,
        data=LeadFollowupUpdate(
            status="cancelled",
            note=data.note,
        ),
    )


# ---------------------------------------------------------------------------
# resolve_followup — the outcome flow. One transaction, classified by the
# Outcome enum, emits 1-2 activity rows and conditionally creates the next
# follow-up. INVARIANTS (do not break):
#   * result_action lands in payload ONLY from this function.
#   * attempt_count++ and the activity insert ride the same commit.
#   * Tenant check happens before any mutation (via followup_repo.get_with_lead).
# ---------------------------------------------------------------------------


# Channel → primary activity type. Resolve flow is call/whatsapp only.
_CHANNEL_TO_ACTIVITY_TYPE: dict[str, LeadActivityType] = {
    "call": LeadActivityType.CALL,
    "whatsapp": LeadActivityType.WHATSAPP,
}

# Outcome buckets — drives the flow. Keep in sync with the Outcome enum AND
# the frontend matrix (outcome-config.ts). "Call me later" (SPOKE_LATER) rides
# the positive flow: contact happened, so it completes + may move stage.
# RETRY outcomes are the only ones that bump attempt_count / feed the tally.
_RETRY = {Outcome.NO_ANSWER, Outcome.BUSY, Outcome.WA_NOT_REPLIED}
_AWAITING = {Outcome.WA_SENT}
_POSITIVE = {Outcome.SPOKE_INTERESTED, Outcome.WA_REPLIED, Outcome.SPOKE_LATER}
_TERMINAL_NOT_INTERESTED = {Outcome.SPOKE_NOT_INTERESTED, Outcome.WA_NOT_INTERESTED}


# Human-readable labels for the activity description fallback. The frontend
# already has the equivalent in `OUTCOME_META[outcome].title`; we duplicate
# the strings here (small, stable map) so the diary stays readable without
# the activity row depending on a payload field for rendering. Deprecated
# outcomes keep labels so historical rows still render if re-described.
_OUTCOME_LABEL: dict[Outcome, str] = {
    Outcome.NO_ANSWER: "No answer",
    Outcome.BUSY: "Busy / cut off",
    Outcome.SPOKE_INTERESTED: "Interested",
    Outcome.SPOKE_LATER: "Call me later",
    Outcome.SPOKE_NOT_INTERESTED: "Not interested",
    Outcome.WA_SENT: "Sent — awaiting reply",
    Outcome.WA_REPLIED: "Replied — interested",
    Outcome.WA_NOT_REPLIED: "Not replied",
    Outcome.WA_NOT_INTERESTED: "Not interested",
    Outcome.WRONG_NUMBER: "Wrong number",
    Outcome.WA_LATER: "Replied — not now",
    Outcome.WA_NO_NUMBER: "Number not on WhatsApp",
}


def _activity_description(channel: str, outcome: Outcome, note: str | None) -> str:
    """Build a non-null description for the primary lead_activities row.

    `lead_activities.description` is NOT NULL (since 0002). When the user
    didn't type a note we synthesize one from the channel + outcome so
    the timeline still reads sensibly — e.g. "WhatsApp · Sent — awaiting
    reply". When a note IS supplied we prefer it verbatim.
    """
    if note and note.strip():
        return note.strip()
    label = _OUTCOME_LABEL.get(outcome, outcome.value.replace("_", " "))
    channel_label = "WhatsApp" if channel == "whatsapp" else "Call"
    return f"{channel_label} · {label}"


def resolve_followup(
    session: Session,
    current_user: User,
    *,
    followup_id: UUID,
    channel: str,
    outcome: Outcome,
    note: str | None = None,
    next_dt: datetime | None = None,
    next_regarding: str | None = None,
    stage_to: str | None = None,
    set_no_followup: bool = False,
) -> dict[str, Any]:
    """Resolve a follow-up with a user-logged outcome.

    Atomic: every write below rides one session.commit() at the end. If
    anything raises after the first mutation, SQLAlchemy rolls back.

    Returns: { followup, lead, next_followup, activities_created }.
    """
    if channel not in _CHANNEL_TO_ACTIVITY_TYPE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="channel must be 'call' or 'whatsapp'",
        )
    activity_type = _CHANNEL_TO_ACTIVITY_TYPE[channel]
    outcome = Outcome(outcome)  # accept raw string too

    # --- 1. Tenant-checked load ---
    followup, lead = followup_repo.get_with_lead(
        session, followup_id, current_user.business_id
    )
    if followup.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Follow-up is already {followup.status}; cannot resolve.",
        )

    activities_created = 0
    next_followup: LeadFollowup | None = None
    result_action: ResultAction

    # --- Timeline-snapshot vars (folded into the primary activity payload at
    # the end, so the diary renders the full story without joins). ---
    resolved_followup_note = followup.note  # topic of the follow-up acted on
    payload_next_dt: datetime | None = None  # resulting reschedule / next date
    payload_next_regarding: str | None = None  # topic of that next follow-up
    moved_to_name: str | None = None  # stage moved into, if any

    def _move_stage(target_name: str) -> None:
        """Move the lead to `target_name` and log one STATUS_CHANGE row.
        Bumps the enclosing `activities_created` via nonlocal; records the
        from/to names for the primary activity payload."""
        nonlocal activities_created, moved_to_name
        new_stage = get_stage_by_name_for_business(
            session, current_user.business_id, target_name
        )
        if new_stage is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Stage '{target_name}' not found for this business",
            )
        moved_to_name = new_stage.name
        old_stage_id = lead.stage_id
        lead_repo.set_stage(session, lead, new_stage.id)
        activity_repo.add(
            session,
            lead_id=lead.id,
            type=LeadActivityType.STATUS_CHANGE,
            description=f"Stage moved to {new_stage.name}",
            followup_id=followup.id,
            created_by=current_user.id,
            payload=safe_jsonify({
                "from_stage_id": old_stage_id,
                "to_stage_id": new_stage.id,
                "to_stage_name": new_stage.name,
            }),
        )
        activities_created += 1

    # --- 2. Classify + mutate state (still no commit) ---
    if outcome in _RETRY:
        # Retry (no_answer / busy / wa_not_replied). Three sub-actions, keyed
        # off what the sheet sends — we never touch last_contacted_at (no
        # contact happened):
        #   * next_dt set            → Reschedule the SAME follow-up.
        #   * stage_to set (no date) → Mark lost: complete + move stage.
        #   * neither                → Just log it: leave the follow-up
        #     pending & on its date so the owner can come back to it. The
        #     tally still climbs because the activity row below is written.
        if next_dt is not None:
            followup_repo.reschedule(
                session,
                followup,
                new_dt=next_dt,
                outcome=outcome.value,
                note=next_regarding,
            )
            payload_next_dt = next_dt
            payload_next_regarding = next_regarding
            if stage_to:
                _move_stage(stage_to)
            result_action = ResultAction.RESCHEDULED
        elif stage_to:
            followup_repo.complete(session, followup, outcome=outcome.value)
            _move_stage(stage_to)
            result_action = ResultAction.CLOSED
        else:
            # Just log — record last_outcome for the list, keep it pending.
            followup.last_outcome = outcome.value
            session.add(followup)
            session.flush()
            result_action = ResultAction.LOGGED

    elif outcome in _AWAITING:
        # WhatsApp sent, waiting for a reply — a pure holding state (note
        # only). Keep the SAME follow-up OPEN and flag it awaiting
        # (last_outcome). Never complete it; no stage move; no
        # last_contacted_at (message sent != conversation); no attempt bump
        # (not a retry). The card surfaces an "Awaiting reply" button to log
        # the resolution (Replied / Not replied / Not interested) later.
        followup.last_outcome = outcome.value
        session.add(followup)
        session.flush()
        result_action = ResultAction.LOGGED

    elif outcome in _POSITIVE:
        # Includes "Call me later" — contact happened. Complete, mark
        # contacted, optionally move stage + open the next follow-up.
        followup_repo.complete(session, followup, outcome=outcome.value)
        lead_repo.touch_contacted(session, lead)
        if stage_to:
            _move_stage(stage_to)
        if not set_no_followup:
            if next_dt is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="next_dt is required unless set_no_followup=true",
                )
            next_followup = followup_repo.create_next(
                session,
                lead_id=lead.id,
                scheduled_dt=next_dt,
                created_by=current_user.id,
                note=next_regarding,
            )
            payload_next_dt = next_dt
            payload_next_regarding = next_regarding
            result_action = ResultAction.NEXT_FOLLOWUP
        else:
            result_action = ResultAction.MARKED_DONE

    elif outcome in _TERMINAL_NOT_INTERESTED:
        # "Not interested" — complete the follow-up. "Mark lost" sends a
        # stage_to (the UI forces Lost); "Just log it" sends none, so we
        # record the outcome without changing the stage.
        followup_repo.complete(session, followup, outcome=outcome.value)
        if stage_to:
            _move_stage(stage_to)
            result_action = ResultAction.CLOSED
        else:
            result_action = ResultAction.MARKED_DONE

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unhandled outcome: {outcome}",
        )

    # --- 3. Primary activity row (always one). Payload v2 snapshots the full
    # story so the timeline renders without joins: outcome, what-we-did-next
    # (result_action + the resulting date/topic), the user's note kept SEPARATE
    # from `description`, the resolved follow-up's topic, and any stage move.
    # `followup_title` (= lead.title) is retained for back-compat.
    primary_payload = safe_jsonify({
        "v": 2,
        "channel": channel,
        "outcome": outcome.value,
        "result_action": result_action.value,
        "attempt": followup.attempt_count,
        # User's free-text note, distinct from the synthesized `description`.
        "note": note.strip() if note and note.strip() else None,
        # Topic of the follow-up that was acted on (its "Regarding").
        "followup_note": resolved_followup_note,
        # The resulting reschedule / next follow-up, if one was set.
        "next_dt": payload_next_dt.isoformat() if payload_next_dt else None,
        "next_regarding": payload_next_regarding,
        # Stage moved into as part of this outcome, if any.
        "to_stage_name": moved_to_name,
        "followup_title": lead.title,
    })
    activity_repo.add(
        session,
        lead_id=lead.id,
        type=activity_type,
        description=_activity_description(channel, outcome, note),
        followup_id=followup.id,
        created_by=current_user.id,
        payload=primary_payload,
    )
    activities_created += 1

    # --- 4. One commit for the whole flow. ---
    session.commit()
    session.refresh(followup)
    session.refresh(lead)
    if next_followup is not None:
        session.refresh(next_followup)

    # 0045 — resolve_followup writes 1–2 activity rows via the no-commit
    # `activity_repo.add()` helper, which bypasses the chokepoint trigger.
    # Fire a single full-rebuild post-commit instead of N incrementals:
    # the incremental path folds ONE new row, so multi-write flows would
    # leave the middle row(s) unrepresented. The rebuild captures
    # everything that just landed in one LLM call.
    if activities_created > 0:
        fire_rebuild_activity_summary(lead.id)

    return {
        "followup": followup,
        "lead": lead,
        "next_followup": next_followup,
        "activities_created": activities_created,
        # Derived after commit so the just-logged outcome is included: retry
        # outcomes accumulate the streak; a positive/terminal resets it to 0.
        # Scoped to the resolved follow-up (its activities carry followup_id).
        "negative_attempts": activity_repo.negative_attempt_breakdown(
            session, followup.id
        ),
    }
