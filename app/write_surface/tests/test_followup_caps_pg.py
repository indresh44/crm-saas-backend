"""Postgres-backed tests for the four follow-up capabilities.

Same shape as test_engine_pg.py + test_lead_caps_pg.py: savepoint-rollback
session, real Postgres, prepare -> commit per capability. For each cap:

  * prepare returns an id + preview (with lead title + scheduled_at)
  * commit performs the correct state change on the row
  * second commit on the same handle is rejected (single-use)
  * editing an editable field flows through to the row
  * editing a locked field is rejected
  * cross-tenant: a follow-up belonging to another business cannot be
    prepared against (read model returns zero rows ViaParent-scoped via
    leads, validate raises ValueError, engine returns INVALID_INPUT)

Run:
    python -m app.write_surface.tests.test_followup_caps_pg
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlmodel import Session

from app.core.database import engine
from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import LeadSource, UserRole
from app.models.lead import Lead
from app.models.lead_followup import LeadFollowup
from app.models.pipeline import Pipeline, PipelineStage
from app.models.prepared_action import STATUS_CONSUMED, PreparedAction
from app.models.user import User
from app.write_surface.engine import ErrorCode, WriteSurfaceError, commit, prepare


# --- Savepoint-rollback session (same pattern as test_engine_pg.py) ---------

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


# --- Fixture helpers --------------------------------------------------------

def _seed_business_user_lead(session: Session, *, label: str = "A") -> tuple[User, UUID]:
    """Insert business + owner user + customer + lead. Returns (user, lead_id).
    Commits within the savepoint so subsequent reads see the rows."""
    bid = uuid4()
    session.add(Business(id=bid, name=f"FU Test {label}",
                         phone=f"99900000{label}"))
    session.flush()
    user = User(
        id=uuid4(), business_id=bid,
        name=f"Owner {label}", email=f"owner-{bid}@test.local",
        role=UserRole.OWNER, is_active=True,
    )
    session.add(user); session.flush()
    pid = uuid4()
    session.add(Pipeline(id=pid, business_id=bid, name="Default", is_default=True))
    session.flush()
    sid = uuid4()
    session.add(PipelineStage(id=sid, pipeline_id=pid, name="New",
                              position=1, color="#888"))
    cust_id = uuid4()
    session.add(Customer(id=cust_id, business_id=bid, name=f"Cust {label}",
                         phone=f"+91 9999900{label}",
                         phone_normalized=f"91999990{label}"))
    session.flush()
    lead_id = uuid4()
    session.add(Lead(id=lead_id, business_id=bid, customer_id=cust_id, stage_id=sid,
                     title=f"Lead {label}", source=LeadSource.WHATSAPP))
    session.commit()
    return user, lead_id


def _seed_pending_followup(session: Session, *, user: User, lead_id: UUID,
                            note: str = "ring back") -> UUID:
    """Insert one PENDING follow-up scheduled in the future. Returns id."""
    fu_id = uuid4()
    session.add(LeadFollowup(
        id=fu_id, lead_id=lead_id,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
        note=note, status="pending", created_by=user.id,
    ))
    session.commit()
    return fu_id


# ===========================================================================
# create_followup
# ===========================================================================

def _new_scheduled_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()


def test_create_followup_prepare_returns_preview():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        handle = prepare(
            s, user,
            capability_name="create_followup",
            raw_inputs={"lead_id": str(lead_id),
                        "scheduled_at": _new_scheduled_at(),
                        "note": "call back about quote"},
        )
        assert isinstance(handle.id, str) and len(handle.id) > 30
        assert "Lead A" in handle.preview
        assert "call back about quote" in handle.preview
        assert sorted(handle.editable_fields) == ["note", "scheduled_at"]


def test_create_followup_commit_creates_row():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        handle = prepare(
            s, user,
            capability_name="create_followup",
            raw_inputs={"lead_id": str(lead_id),
                        "scheduled_at": _new_scheduled_at()},
        )
        result = commit(s, user, id=handle.id)
        assert "followup_id" in result and result["status"] == "pending"
        s.expire_all()
        row = s.get(LeadFollowup, UUID(result["followup_id"]))
        assert row is not None and row.lead_id == lead_id
        assert s.get(PreparedAction, handle.id).status == STATUS_CONSUMED


def test_create_followup_second_commit_rejected():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        handle = prepare(
            s, user, capability_name="create_followup",
            raw_inputs={"lead_id": str(lead_id),
                        "scheduled_at": _new_scheduled_at()},
        )
        commit(s, user, id=handle.id)
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_create_followup_edit_editable_field_flows_through():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        handle = prepare(
            s, user, capability_name="create_followup",
            raw_inputs={"lead_id": str(lead_id),
                        "scheduled_at": _new_scheduled_at()},
        )
        new_when = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        result = commit(s, user, id=handle.id, edited_fields={
            "scheduled_at": new_when,
            "note": "edited note",
        })
        row = s.get(LeadFollowup, UUID(result["followup_id"]))
        assert row.note == "edited note"
        assert row.scheduled_at.isoformat().startswith(new_when[:19])


def test_create_followup_edit_locked_field_rejected():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        handle = prepare(
            s, user, capability_name="create_followup",
            raw_inputs={"lead_id": str(lead_id),
                        "scheduled_at": _new_scheduled_at()},
        )
        try:
            commit(s, user, id=handle.id, edited_fields={"lead_id": str(uuid4())})
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "lead_id" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for locked-field edit")


def test_create_followup_cross_tenant_lead_rejected():
    """User from business B cannot schedule a follow-up on a lead in
    business A. The validate hook reads `leads` via the read model — which
    returns zero rows because the lead is tenant-scoped to A."""
    with _rollback_session() as s:
        _user_a, lead_a = _seed_business_user_lead(s, label="A")
        user_b, _lead_b = _seed_business_user_lead(s, label="B")
        try:
            prepare(
                s, user_b, capability_name="create_followup",
                raw_inputs={"lead_id": str(lead_a),
                            "scheduled_at": _new_scheduled_at()},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "not found" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT cross-tenant")


# ===========================================================================
# complete_followup
# ===========================================================================

def test_complete_followup_prepare_returns_preview():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id, note="ring")
        handle = prepare(
            s, user, capability_name="complete_followup",
            raw_inputs={"followup_id": str(fu_id), "note": "spoke to customer"},
        )
        assert "Lead A" in handle.preview
        assert "spoke to customer" in handle.preview
        assert sorted(handle.editable_fields) == ["note"]


def test_complete_followup_commit_marks_done():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id)
        handle = prepare(
            s, user, capability_name="complete_followup",
            raw_inputs={"followup_id": str(fu_id)},
        )
        result = commit(s, user, id=handle.id)
        assert result["status"] == "done" and result["completed_at"] is not None
        s.expire_all()
        row = s.get(LeadFollowup, fu_id)
        assert row.status == "done" and row.completed_at is not None


def test_complete_followup_second_commit_rejected():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id)
        handle = prepare(
            s, user, capability_name="complete_followup",
            raw_inputs={"followup_id": str(fu_id)},
        )
        commit(s, user, id=handle.id)
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_complete_followup_edit_editable_note_flows_through():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id, note="original")
        handle = prepare(
            s, user, capability_name="complete_followup",
            raw_inputs={"followup_id": str(fu_id)},
        )
        commit(s, user, id=handle.id, edited_fields={"note": "actually wrapped up via WhatsApp"})
        s.expire_all()
        row = s.get(LeadFollowup, fu_id)
        assert row.note == "actually wrapped up via WhatsApp"
        assert row.status == "done"


def test_complete_followup_edit_locked_field_rejected():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id)
        handle = prepare(
            s, user, capability_name="complete_followup",
            raw_inputs={"followup_id": str(fu_id)},
        )
        try:
            commit(s, user, id=handle.id, edited_fields={"followup_id": str(uuid4())})
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "followup_id" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for locked-field edit")


def test_complete_followup_cross_tenant_rejected():
    with _rollback_session() as s:
        user_a, lead_a = _seed_business_user_lead(s, label="A")
        fu_a = _seed_pending_followup(s, user=user_a, lead_id=lead_a)
        user_b, _lead_b = _seed_business_user_lead(s, label="B")
        try:
            prepare(
                s, user_b, capability_name="complete_followup",
                raw_inputs={"followup_id": str(fu_a)},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "not found" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT cross-tenant")


# ===========================================================================
# reschedule_followup
# ===========================================================================

def test_reschedule_followup_prepare_shows_old_to_new():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id)
        new_when = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        handle = prepare(
            s, user, capability_name="reschedule_followup",
            raw_inputs={"followup_id": str(fu_id), "scheduled_at": new_when},
        )
        assert "Lead A" in handle.preview
        assert "from" in handle.preview and "to" in handle.preview
        assert new_when[:16] in handle.preview     # ISO down to minute
        assert sorted(handle.editable_fields) == ["note", "scheduled_at"]


def test_reschedule_followup_commit_updates_scheduled_at():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id)
        new_when = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        handle = prepare(
            s, user, capability_name="reschedule_followup",
            raw_inputs={"followup_id": str(fu_id), "scheduled_at": new_when},
        )
        commit(s, user, id=handle.id)
        s.expire_all()
        row = s.get(LeadFollowup, fu_id)
        assert row.scheduled_at.isoformat().startswith(new_when[:19])
        assert row.status == "pending"


def test_reschedule_followup_second_commit_rejected():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id)
        new_when = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        handle = prepare(
            s, user, capability_name="reschedule_followup",
            raw_inputs={"followup_id": str(fu_id), "scheduled_at": new_when},
        )
        commit(s, user, id=handle.id)
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_reschedule_followup_edit_editable_field_flows_through():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id)
        proposed = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        handle = prepare(
            s, user, capability_name="reschedule_followup",
            raw_inputs={"followup_id": str(fu_id), "scheduled_at": proposed},
        )
        edited = (datetime.now(timezone.utc) + timedelta(days=9)).isoformat()
        commit(s, user, id=handle.id, edited_fields={
            "scheduled_at": edited, "note": "human-picked time",
        })
        s.expire_all()
        row = s.get(LeadFollowup, fu_id)
        assert row.scheduled_at.isoformat().startswith(edited[:19])
        assert row.note == "human-picked time"


def test_reschedule_followup_edit_locked_field_rejected():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id)
        handle = prepare(
            s, user, capability_name="reschedule_followup",
            raw_inputs={"followup_id": str(fu_id),
                        "scheduled_at": _new_scheduled_at()},
        )
        try:
            commit(s, user, id=handle.id, edited_fields={"followup_id": str(uuid4())})
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
        else:
            raise AssertionError("expected INVALID_INPUT for locked-field edit")


def test_reschedule_followup_cross_tenant_rejected():
    with _rollback_session() as s:
        user_a, lead_a = _seed_business_user_lead(s, label="A")
        fu_a = _seed_pending_followup(s, user=user_a, lead_id=lead_a)
        user_b, _lead_b = _seed_business_user_lead(s, label="B")
        try:
            prepare(
                s, user_b, capability_name="reschedule_followup",
                raw_inputs={"followup_id": str(fu_a),
                            "scheduled_at": _new_scheduled_at()},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "not found" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT cross-tenant")


def test_reschedule_followup_done_followup_rejected_at_prepare():
    """validate() mirrors the service's terminal-state rule: cannot reschedule
    a done follow-up. LLM sees the reason at prepare time, not EXECUTE_FAILED."""
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id)
        fu = s.get(LeadFollowup, fu_id)
        fu.status = "done"
        fu.completed_at = datetime.now(timezone.utc)
        s.add(fu); s.commit()
        try:
            prepare(
                s, user, capability_name="reschedule_followup",
                raw_inputs={"followup_id": str(fu_id),
                            "scheduled_at": _new_scheduled_at()},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "done" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for done follow-up")


# ===========================================================================
# cancel_followup
# ===========================================================================

def test_cancel_followup_prepare_returns_preview():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id)
        handle = prepare(
            s, user, capability_name="cancel_followup",
            raw_inputs={"followup_id": str(fu_id), "note": "customer dropped off"},
        )
        assert "Lead A" in handle.preview
        assert "customer dropped off" in handle.preview
        assert sorted(handle.editable_fields) == ["note"]


def test_cancel_followup_commit_marks_cancelled():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id)
        handle = prepare(
            s, user, capability_name="cancel_followup",
            raw_inputs={"followup_id": str(fu_id)},
        )
        result = commit(s, user, id=handle.id)
        assert result["status"] == "cancelled"
        s.expire_all()
        row = s.get(LeadFollowup, fu_id)
        assert row.status == "cancelled"


def test_cancel_followup_second_commit_rejected():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id)
        handle = prepare(
            s, user, capability_name="cancel_followup",
            raw_inputs={"followup_id": str(fu_id)},
        )
        commit(s, user, id=handle.id)
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_cancel_followup_edit_editable_note_flows_through():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id, note="original")
        handle = prepare(
            s, user, capability_name="cancel_followup",
            raw_inputs={"followup_id": str(fu_id)},
        )
        commit(s, user, id=handle.id, edited_fields={"note": "human edited reason"})
        s.expire_all()
        row = s.get(LeadFollowup, fu_id)
        assert row.note == "human edited reason"
        assert row.status == "cancelled"


def test_cancel_followup_edit_locked_field_rejected():
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id)
        handle = prepare(
            s, user, capability_name="cancel_followup",
            raw_inputs={"followup_id": str(fu_id)},
        )
        try:
            commit(s, user, id=handle.id, edited_fields={"followup_id": str(uuid4())})
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
        else:
            raise AssertionError("expected INVALID_INPUT for locked-field edit")


def test_cancel_followup_cross_tenant_rejected():
    with _rollback_session() as s:
        user_a, lead_a = _seed_business_user_lead(s, label="A")
        fu_a = _seed_pending_followup(s, user=user_a, lead_id=lead_a)
        user_b, _lead_b = _seed_business_user_lead(s, label="B")
        try:
            prepare(
                s, user_b, capability_name="cancel_followup",
                raw_inputs={"followup_id": str(fu_a)},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "not found" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT cross-tenant")


def test_cancel_followup_done_rejected_at_prepare():
    """validate() refuses to cancel an already-done follow-up — preview-time
    error, not EXECUTE_FAILED at commit."""
    with _rollback_session() as s:
        user, lead_id = _seed_business_user_lead(s)
        fu_id = _seed_pending_followup(s, user=user, lead_id=lead_id)
        fu = s.get(LeadFollowup, fu_id)
        fu.status = "done"; fu.completed_at = datetime.now(timezone.utc)
        s.add(fu); s.commit()
        try:
            prepare(
                s, user, capability_name="cancel_followup",
                raw_inputs={"followup_id": str(fu_id)},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "done" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for done follow-up")


# ---------------------------------------------------------------------------
# Runner — skips cleanly if DB unreachable
# ---------------------------------------------------------------------------

def _run() -> int:
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP  database unreachable ({type(exc).__name__}: {str(exc)[:120]})")
        return 0

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run())
