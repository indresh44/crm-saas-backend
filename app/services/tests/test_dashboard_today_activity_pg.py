"""Postgres-backed tests for the dashboard "Today's Activity" feed.

Covers:
  * Owner (HUMAN) activities today are returned newest-first, with
    lead_title + customer_name stitched from the JOIN.
  * actor_type filter: AI / TASK / SYSTEM rows are excluded (they belong to
    the assistant section, not the owner's diary).
  * Low-signal types (lead_updated, payment_voided, ...) are excluded.
  * Resolve-flow de-dup: a status_change row carrying a followup_id is
    suppressed (rendered by its paired call/whatsapp row); a manual
    status_change (followup_id NULL) is kept.
  * Payload lift: call/whatsapp rows surface outcome / next_dt / to_stage_name.
  * money_collected_today sums payment_recorded payload amounts.
  * counts_by_type + total reflect the qualifying set.
  * Cross-tenant: a foreign business sees none of tenant A's activity.

Run:
    python -m app.services.tests.test_dashboard_today_activity_pg
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlmodel import Session

from app.core.actor_context import set_actor_context
from app.core.database import engine
from app.core.phone import normalize_phone_value
from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import ActorType, LeadActivityType, UserRole
from app.models.lead import Lead, LeadActivity
from app.models.lead_followup import LeadFollowup
from app.models.pipeline import Pipeline, PipelineStage
from app.models.user import User
from app.services import dashboard_service, lead_service


# ---------------------------------------------------------------------------
# Savepoint-rollback session (mirrors the assistant-tasks test)
# ---------------------------------------------------------------------------

@contextmanager
def _rollback_session():
    connection = engine.connect()
    outer_tx = connection.begin()
    session = Session(bind=connection)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, transaction):
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    try:
        yield session
    finally:
        event.remove(session, "after_transaction_end", _restart_savepoint)
        session.close()
        outer_tx.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@dataclass
class _Fix:
    user: User
    lead_id: UUID
    customer_name: str
    lead_title: str
    stage_id: UUID        # the lead's current stage
    next_stage_id: UUID   # a second stage to move into


def _seed(session: Session, *, name: str = "Diary Co") -> _Fix:
    bid = uuid4()
    session.add(Business(id=bid, name=name, phone=f"99{uuid4().int % 100000000:08d}",
                         timezone="Asia/Kolkata"))
    session.flush()
    user = User(id=uuid4(), business_id=bid, name="Owner",
                email=f"owner-{bid}@test.local",
                role=UserRole.OWNER, is_active=True)
    session.add(user); session.flush()
    pid = uuid4()
    session.add(Pipeline(id=pid, business_id=bid, name="Default", is_default=True))
    session.flush()
    sid = uuid4()
    session.add(PipelineStage(id=sid, pipeline_id=pid, name="New Enquiry",
                              position=1, color="#888"))
    sid2 = uuid4()
    session.add(PipelineStage(id=sid2, pipeline_id=pid, name="Quote Sent",
                              position=2, color="#999"))
    session.flush()
    cid = uuid4()
    cname = "Rajesh Mehta"
    session.add(Customer(id=cid, business_id=bid, name=cname,
                         phone="+91 99999 00001",
                         phone_normalized=normalize_phone_value("+91 99999 00001")))
    session.flush()
    lid = uuid4()
    title = "Modular kitchen"
    session.add(Lead(id=lid, business_id=bid, stage_id=sid, customer_id=cid,
                     title=title))
    session.commit()
    return _Fix(user=user, lead_id=lid, customer_name=cname, lead_title=title,
                stage_id=sid, next_stage_id=sid2)


def _act(
    session: Session,
    fix: _Fix,
    *,
    type: LeadActivityType,
    description: str,
    minutes_ago: int,
    actor_type: ActorType | None = ActorType.HUMAN,
    payload: dict | None = None,
    followup_id: UUID | None = None,
) -> LeadActivity:
    """Insert one activity with an explicit created_at (distinct + today) so
    ordering is deterministic and the row falls inside today's IST bounds."""
    row = LeadActivity(
        lead_id=fix.lead_id,
        type=type,
        description=description,
        created_by=fix.user.id,
        actor_type=actor_type,
        payload=payload,
        followup_id=followup_id,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )
    session.add(row); session.commit()
    return row


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_returns_human_rows_newest_first_with_names_and_money():
    with _rollback_session() as s:
        f = _seed(s)
        _act(s, f, type=LeadActivityType.LEAD_CREATED,
             description="New enquiry", minutes_ago=30)
        _act(s, f, type=LeadActivityType.PAYMENT_RECORDED,
             description="Payment of ₹25,000.00 recorded for INV-008",
             minutes_ago=20, payload={"amount": 25000})
        _act(s, f, type=LeadActivityType.CALL,
             description="Call · Interested", minutes_ago=5,
             payload={"outcome": "spoke_interested"})

        resp = dashboard_service.get_today_activity(s, f.user)

        assert resp.total == 3
        assert len(resp.items) == 3
        # Newest-first: the call (5 min ago) leads, lead_created (30) last.
        assert resp.items[0].type == "call"
        assert resp.items[-1].type == "lead_created"
        # Names stitched from the JOIN.
        assert resp.items[0].customer_name == f.customer_name
        assert resp.items[0].lead_title == f.lead_title
        # Summary rollups.
        assert resp.money_collected_today == 25000.0
        assert resp.counts_by_type["payment_recorded"] == 1
        assert resp.counts_by_type["call"] == 1
        assert resp.counts_by_type["lead_created"] == 1


def test_excludes_non_human_actors_and_low_signal_types():
    with _rollback_session() as s:
        f = _seed(s)
        # Kept.
        _act(s, f, type=LeadActivityType.FOLLOWUP_COMPLETED,
             description="Follow-up marked as done", minutes_ago=10)
        # Excluded — AI actor (assistant section's job).
        _act(s, f, type=LeadActivityType.CALL, description="AI call",
             minutes_ago=9, actor_type=ActorType.AI)
        # Excluded — SYSTEM actor.
        _act(s, f, type=LeadActivityType.WHATSAPP, description="webhook",
             minutes_ago=8, actor_type=ActorType.SYSTEM)
        # Excluded — low-signal types even when HUMAN.
        _act(s, f, type=LeadActivityType.LEAD_UPDATED,
             description="edited", minutes_ago=7)
        _act(s, f, type=LeadActivityType.PAYMENT_VOIDED,
             description="voided", minutes_ago=6)

        resp = dashboard_service.get_today_activity(s, f.user)

        assert resp.total == 1
        assert [i.type for i in resp.items] == ["followup_completed"]


def test_shows_all_stage_changes_followup_linked_or_not():
    """Every stage change shows — whether it was a standalone move or moved
    while resolving a follow-up. We don't hide any of today's activity."""
    with _rollback_session() as s:
        f = _seed(s)
        fu = LeadFollowup(
            id=uuid4(), lead_id=f.lead_id,
            scheduled_at=datetime.now(timezone.utc),
            created_by=f.user.id, status="done",
        )
        s.add(fu); s.commit()
        # Moved while resolving a follow-up.
        _act(s, f, type=LeadActivityType.STATUS_CHANGE,
             description="Stage moved to Quote Sent", minutes_ago=10,
             followup_id=fu.id)
        # Standalone manual move.
        _act(s, f, type=LeadActivityType.STATUS_CHANGE,
             description="Stage moved to Approved", minutes_ago=5)

        resp = dashboard_service.get_today_activity(s, f.user)

        assert resp.total == 2
        descriptions = {i.description for i in resp.items}
        assert "Stage moved to Quote Sent" in descriptions
        assert "Stage moved to Approved" in descriptions


def test_lifts_payload_fields_for_resolve_rows():
    with _rollback_session() as s:
        f = _seed(s)
        _act(s, f, type=LeadActivityType.WHATSAPP,
             description="asking for quote", minutes_ago=5,
             payload={
                 "channel": "whatsapp",
                 "outcome": "wa_replied",
                 "result_action": "next_followup",
                 "note": "will send revised quote",
                 "followup_note": "asking for quote",
                 "next_dt": "2026-06-12T05:30:00+00:00",
                 "to_stage_name": "Quote Sent",
             })

        resp = dashboard_service.get_today_activity(s, f.user)
        item = resp.items[0]
        assert item.channel == "whatsapp"
        assert item.outcome == "wa_replied"
        assert item.result_action == "next_followup"
        assert item.note == "will send revised quote"
        assert item.followup_note == "asking for quote"
        assert item.next_dt == "2026-06-12T05:30:00+00:00"
        assert item.to_stage_name == "Quote Sent"


def test_standalone_stage_move_shows_as_status_change():
    """END-TO-END: a manual stage move via lead_service.move_lead_stage (the
    `POST /leads/{id}/move` path) under the HTTP HUMAN actor context must
    surface in the feed as a status_change row. This is the exact path the
    UI's stage control uses — if it doesn't appear, the feature is broken
    for the most common stage change."""
    with _rollback_session() as s:
        f = _seed(s)
        with set_actor_context(ActorType.HUMAN):
            lead_service.move_lead_stage(
                session=s,
                current_user=f.user,
                lead_id=f.lead_id,
                new_stage_id=f.next_stage_id,
            )

        resp = dashboard_service.get_today_activity(s, f.user)

        status_rows = [i for i in resp.items if i.type == "status_change"]
        assert len(status_rows) == 1, (
            f"expected 1 status_change row, got {len(status_rows)}; "
            f"all types={[i.type for i in resp.items]}"
        )
        assert "Quote Sent" in status_rows[0].description


def test_cross_tenant_isolation():
    with _rollback_session() as s:
        a = _seed(s, name="Tenant A")
        b = _seed(s, name="Tenant B")
        _act(s, a, type=LeadActivityType.PAYMENT_RECORDED,
             description="A's payment", minutes_ago=5, payload={"amount": 999})

        resp_b = dashboard_service.get_today_activity(s, b.user)
        assert resp_b.total == 0
        assert resp_b.items == []
        assert resp_b.money_collected_today == 0.0


def test_limit_caps_items_but_total_counts_all():
    with _rollback_session() as s:
        f = _seed(s)
        for i in range(5):
            _act(s, f, type=LeadActivityType.NOTE,
                 description=f"note {i}", minutes_ago=i + 1)

        resp = dashboard_service.get_today_activity(s, f.user, limit=2)
        assert resp.total == 5
        assert len(resp.items) == 2
        assert resp.counts_by_type["note"] == 5


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run():
    tests = [
        test_returns_human_rows_newest_first_with_names_and_money,
        test_excludes_non_human_actors_and_low_signal_types,
        test_shows_all_stage_changes_followup_linked_or_not,
        test_lifts_payload_fields_for_resolve_rows,
        test_standalone_stage_move_shows_as_status_change,
        test_cross_tenant_isolation,
        test_limit_caps_items_but_total_counts_all,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
            passed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL  {t.__name__}: {exc!r}")
    print(f"\n{passed}/{len(tests)} passed")
    if passed != len(tests):
        raise SystemExit(1)


if __name__ == "__main__":
    _run()
