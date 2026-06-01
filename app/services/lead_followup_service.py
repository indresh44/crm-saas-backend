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

# Outcome buckets — drives the flow. Keep in sync with the Outcome enum.
_NO_CONTACT = {Outcome.NO_ANSWER, Outcome.BUSY}
_SENT = {Outcome.WA_SENT}
_POSITIVE = {Outcome.SPOKE_INTERESTED, Outcome.WA_REPLIED}
_NEUTRAL = {Outcome.SPOKE_LATER, Outcome.WA_LATER}
_TERMINAL_NOT_INTERESTED = {Outcome.SPOKE_NOT_INTERESTED}
_WRONG_NUMBER = {Outcome.WRONG_NUMBER}
_WA_NO_NUMBER = {Outcome.WA_NO_NUMBER}  # treated as no-contact reschedule


# Human-readable labels for the activity description fallback. The frontend
# already has the equivalent in `OUTCOME_META[outcome].title`; we duplicate
# the strings here (small, stable map) so the diary stays readable without
# the activity row depending on a payload field for rendering.
_OUTCOME_LABEL: dict[Outcome, str] = {
    Outcome.NO_ANSWER: "No answer",
    Outcome.BUSY: "Busy / cut off",
    Outcome.WRONG_NUMBER: "Wrong number",
    Outcome.SPOKE_INTERESTED: "Interested",
    Outcome.SPOKE_LATER: "Call me later",
    Outcome.SPOKE_NOT_INTERESTED: "Not interested",
    Outcome.WA_SENT: "Sent — awaiting reply",
    Outcome.WA_REPLIED: "Replied — interested",
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

    # --- 2. Classify + mutate state (still no commit) ---
    if outcome in _NO_CONTACT or outcome in _WA_NO_NUMBER:
        # No-contact: stay pending, bump attempts, push the date out.
        if next_dt is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="next_dt is required for a no-contact outcome",
            )
        followup_repo.reschedule(
            session, followup, new_dt=next_dt, outcome=outcome.value
        )
        result_action = ResultAction.RESCHEDULED
        # Intentionally NOT touching lead.last_contacted_at — there was
        # no contact.

    elif outcome in _SENT:
        # WhatsApp sent (no reply yet). Closes this attempt, optionally
        # opens a chase follow-up. Default chase = +2 days.
        followup_repo.complete(session, followup, outcome=outcome.value)
        if not set_no_followup:
            chase_at = next_dt or (datetime.now(timezone.utc) + timedelta(days=2))
            next_followup = followup_repo.create_next(
                session,
                lead_id=lead.id,
                scheduled_dt=chase_at,
                created_by=current_user.id,
                followup_type=channel,
            )
            result_action = ResultAction.NEXT_FOLLOWUP
        else:
            result_action = ResultAction.MARKED_DONE
        # Do NOT touch last_contacted_at — message sent ≠ conversation.

    elif outcome in _POSITIVE:
        followup_repo.complete(session, followup, outcome=outcome.value)
        lead_repo.touch_contacted(session, lead)

        if stage_to:
            new_stage = get_stage_by_name_for_business(
                session, current_user.business_id, stage_to
            )
            if new_stage is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Stage '{stage_to}' not found for this business",
                )
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
                followup_type=channel,
            )
            result_action = ResultAction.NEXT_FOLLOWUP
        else:
            result_action = ResultAction.MARKED_DONE

    elif outcome in _NEUTRAL:
        followup_repo.complete(session, followup, outcome=outcome.value)
        lead_repo.touch_contacted(session, lead)
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
                followup_type=channel,
            )
            result_action = ResultAction.NEXT_FOLLOWUP
        else:
            result_action = ResultAction.MARKED_DONE

    elif outcome in _TERMINAL_NOT_INTERESTED:
        followup_repo.complete(session, followup, outcome=outcome.value)
        # Lost-stage handling — by convention every persona pipeline has
        # a stage literally named "Lost" (see pipeline_service.PIPELINE_TEMPLATES).
        lost_stage = get_stage_by_name_for_business(
            session, current_user.business_id, "Lost"
        )
        if lost_stage is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No 'Lost' stage configured for this business",
            )
        old_stage_id = lead.stage_id
        lead_repo.set_stage(session, lead, lost_stage.id)
        activity_repo.add(
            session,
            lead_id=lead.id,
            type=LeadActivityType.STATUS_CHANGE,
            description="Stage moved to Lost",
            followup_id=followup.id,
            created_by=current_user.id,
            payload=safe_jsonify({
                "from_stage_id": old_stage_id,
                "to_stage_id": lost_stage.id,
                "to_stage_name": lost_stage.name,
            }),
        )
        activities_created += 1
        result_action = ResultAction.CLOSED

    elif outcome in _WRONG_NUMBER:
        followup_repo.complete(session, followup, outcome=outcome.value)
        lead_repo.flag_phone(session, lead)
        result_action = ResultAction.CLOSED

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unhandled outcome: {outcome}",
        )

    # --- 3. Primary activity row (always one). The `attempt` value is
    # the followup's attempt_count AFTER the mutation above. `followup_title`
    # snapshots lead.title — lead_followups has no title column, and the
    # lead title is the stable string describing what the follow-up was for.
    primary_payload = safe_jsonify({
        "outcome": outcome.value,
        "result_action": result_action.value,
        "attempt": followup.attempt_count,
        "followup_title": lead.title,
        "channel": channel,
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
    }
