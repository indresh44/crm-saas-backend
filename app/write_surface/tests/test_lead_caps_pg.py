"""Postgres-backed tests for the three lead-related write-surface capabilities:
create_lead, update_lead_stage, add_lead_note.

Same fixture + savepoint-rollback pattern as test_engine_pg.py.

Run:
    python -m app.write_surface.tests.test_lead_caps_pg
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import event, select
from sqlmodel import Session

from app.core.database import engine
from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import LeadActivityType, LeadSource, UserRole
from app.models.lead import Lead, LeadActivity
from app.models.pipeline import Pipeline, PipelineStage
from app.models.prepared_action import STATUS_CONSUMED, PreparedAction
from app.models.user import User
from app.write_surface.engine import ErrorCode, WriteSurfaceError, commit, prepare


# --- Savepoint-rollback session (duplicated from test_engine_pg.py / store tests) ---

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


# --- Fixture: business + user + pipeline + 2 stages + customer + 1 lead -----

@dataclass
class _Fix:
    user: User
    pipeline_id: UUID
    stage1_id: UUID      # "New"
    stage2_id: UUID      # "WIP"
    customer_id: UUID
    lead_id: UUID        # in stage1


def _seed(session: Session, bid: UUID) -> _Fix:
    session.add(Business(id=bid, name="LS Test Co", phone="9990000000"))
    session.flush()
    user = User(
        id=uuid4(), business_id=bid, name="Owner",
        email=f"owner-{bid}@test.local", role=UserRole.OWNER, is_active=True,
    )
    session.add(user)
    session.flush()
    pid = uuid4()
    session.add(Pipeline(id=pid, business_id=bid, name="Default", is_default=True))
    session.flush()
    s1, s2 = uuid4(), uuid4()
    session.add(PipelineStage(id=s1, pipeline_id=pid, name="New", position=1, color="#888"))
    session.add(PipelineStage(id=s2, pipeline_id=pid, name="WIP", position=3, color="#0a0"))
    session.flush()
    cid = uuid4()
    session.add(Customer(
        id=cid, business_id=bid, name="Rajesh",
        phone="+91 99999 00001", phone_normalized="919999900001",
    ))
    session.flush()
    lid = uuid4()
    session.add(Lead(
        id=lid, business_id=bid, customer_id=cid, stage_id=s1,
        title="Test Lead", source=LeadSource.WHATSAPP,
    ))
    session.commit()
    return _Fix(user=user, pipeline_id=pid, stage1_id=s1, stage2_id=s2,
                customer_id=cid, lead_id=lid)


# ===========================================================================
# create_lead — 5 tests
# ===========================================================================

def test_create_lead_prepare_returns_id_and_preview():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        h = prepare(s, f.user, capability_name="create_lead", raw_inputs={
            "stage_id": str(f.stage1_id), "title": "Kitchen Reno",
            "customer_id": str(f.customer_id), "source": "whatsapp",
            "estimated_value": "250000.00", "notes": "hot lead",
        })
        assert isinstance(h.id, str) and len(h.id) > 30
        assert "Kitchen Reno" in h.preview
        assert "New" in h.preview      # stage name
        assert sorted(h.editable_fields) == ["estimated_value", "notes"]


def test_create_lead_commit_creates_row():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        h = prepare(s, f.user, capability_name="create_lead", raw_inputs={
            "stage_id": str(f.stage1_id), "title": "Kitchen Reno",
            "customer_id": str(f.customer_id), "source": "whatsapp",
            "estimated_value": "250000.00", "notes": "hot lead",
        })
        result = commit(s, f.user, id=h.id)
        lead = s.get(Lead, UUID(result["lead_id"]))
        assert lead.title == "Kitchen Reno"
        assert lead.stage_id == f.stage1_id
        assert lead.customer_id == f.customer_id
        assert lead.source == LeadSource.WHATSAPP
        assert lead.estimated_value == Decimal("250000.00")
        assert lead.notes == "hot lead"
        assert s.get(PreparedAction, h.id).status == STATUS_CONSUMED


def test_create_lead_second_commit_rejected():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        h = prepare(s, f.user, capability_name="create_lead", raw_inputs={
            "stage_id": str(f.stage1_id), "title": "X",
        })
        commit(s, f.user, id=h.id)
        try:
            commit(s, f.user, id=h.id)
        except WriteSurfaceError as e:
            assert e.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_create_lead_editing_editable_field_works():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        h = prepare(s, f.user, capability_name="create_lead", raw_inputs={
            "stage_id": str(f.stage1_id), "title": "X", "notes": "original",
        })
        result = commit(s, f.user, id=h.id, edited_fields={
            "notes": "edited at confirm time",
            "estimated_value": "99999.99",
        })
        lead = s.get(Lead, UUID(result["lead_id"]))
        assert lead.notes == "edited at confirm time"
        assert lead.estimated_value == Decimal("99999.99")


def test_create_lead_editing_locked_field_rejected():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        h = prepare(s, f.user, capability_name="create_lead", raw_inputs={
            "stage_id": str(f.stage1_id), "title": "Real Title",
        })
        try:
            commit(s, f.user, id=h.id, edited_fields={"title": "Hijacked"})
        except WriteSurfaceError as e:
            assert e.code == ErrorCode.INVALID_INPUT
            assert "title" in e.message
        else:
            raise AssertionError("expected INVALID_INPUT for editing locked title")


# ===========================================================================
# update_lead_stage — 4 tests (no editable fields; the "edit flows through"
# case from the spec is N/A — see test_update_lead_stage_locked_field_rejected
# which proves the editable-name whitelist is empty).
# ===========================================================================

def test_update_lead_stage_prepare_returns_id_and_preview():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        h = prepare(s, f.user, capability_name="update_lead_stage", raw_inputs={
            "lead_id": str(f.lead_id), "new_stage_id": str(f.stage2_id),
        })
        assert "Test Lead" in h.preview
        assert "New" in h.preview and "WIP" in h.preview     # from -> to
        assert h.editable_fields == ()


def test_update_lead_stage_commit_moves_the_lead():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        h = prepare(s, f.user, capability_name="update_lead_stage", raw_inputs={
            "lead_id": str(f.lead_id), "new_stage_id": str(f.stage2_id),
        })
        result = commit(s, f.user, id=h.id)
        assert UUID(result["stage_id"]) == f.stage2_id
        s.expire_all()
        assert s.get(Lead, f.lead_id).stage_id == f.stage2_id
        # And a STATUS_CHANGE activity row was written by the service.
        rows = s.execute(
            select(LeadActivity).where(
                LeadActivity.lead_id == f.lead_id,
                LeadActivity.type == LeadActivityType.STATUS_CHANGE,
            )
        ).all()
        assert len(rows) == 1


def test_update_lead_stage_second_commit_rejected():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        h = prepare(s, f.user, capability_name="update_lead_stage", raw_inputs={
            "lead_id": str(f.lead_id), "new_stage_id": str(f.stage2_id),
        })
        commit(s, f.user, id=h.id)
        try:
            commit(s, f.user, id=h.id)
        except WriteSurfaceError as e:
            assert e.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_update_lead_stage_locked_field_rejected():
    """All inputs are locked — any edited_field is rejected."""
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        h = prepare(s, f.user, capability_name="update_lead_stage", raw_inputs={
            "lead_id": str(f.lead_id), "new_stage_id": str(f.stage2_id),
        })
        try:
            commit(s, f.user, id=h.id, edited_fields={"new_stage_id": str(uuid4())})
        except WriteSurfaceError as e:
            assert e.code == ErrorCode.INVALID_INPUT
            assert "new_stage_id" in e.message
        else:
            raise AssertionError("expected INVALID_INPUT for editing locked new_stage_id")


# ===========================================================================
# add_lead_note — 5 tests
# ===========================================================================

def test_add_lead_note_prepare_returns_id_and_preview():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        h = prepare(s, f.user, capability_name="add_lead_note", raw_inputs={
            "lead_id": str(f.lead_id), "description": "Called customer — needs revised quote.",
        })
        assert "Test Lead" in h.preview
        assert "revised quote" in h.preview
        assert h.editable_fields == ("description",)


def test_add_lead_note_commit_writes_NOTE_activity():
    """Type is hard-coded to NOTE — never read from inputs."""
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        h = prepare(s, f.user, capability_name="add_lead_note", raw_inputs={
            "lead_id": str(f.lead_id), "description": "original",
        })
        result = commit(s, f.user, id=h.id)
        activity = s.get(LeadActivity, UUID(result["activity_id"]))
        assert activity.type == LeadActivityType.NOTE
        assert activity.lead_id == f.lead_id
        assert activity.description == "original"
        assert activity.created_by == f.user.id


def test_add_lead_note_second_commit_rejected():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        h = prepare(s, f.user, capability_name="add_lead_note", raw_inputs={
            "lead_id": str(f.lead_id), "description": "n",
        })
        commit(s, f.user, id=h.id)
        try:
            commit(s, f.user, id=h.id)
        except WriteSurfaceError as e:
            assert e.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_add_lead_note_editing_description_flows_through():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        h = prepare(s, f.user, capability_name="add_lead_note", raw_inputs={
            "lead_id": str(f.lead_id), "description": "draft",
        })
        result = commit(s, f.user, id=h.id, edited_fields={
            "description": "the human rewrote this before clicking confirm",
        })
        activity = s.get(LeadActivity, UUID(result["activity_id"]))
        assert activity.description == "the human rewrote this before clicking confirm"
        assert activity.type == LeadActivityType.NOTE   # still NOTE


def test_add_lead_note_editing_locked_lead_id_rejected():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        h = prepare(s, f.user, capability_name="add_lead_note", raw_inputs={
            "lead_id": str(f.lead_id), "description": "n",
        })
        try:
            commit(s, f.user, id=h.id, edited_fields={"lead_id": str(uuid4())})
        except WriteSurfaceError as e:
            assert e.code == ErrorCode.INVALID_INPUT
            assert "lead_id" in e.message
        else:
            raise AssertionError("expected INVALID_INPUT for editing locked lead_id")


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
