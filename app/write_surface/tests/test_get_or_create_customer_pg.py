"""Postgres-backed tests for the get_or_create_customer capability.

Same fixture + savepoint-rollback pattern as the other write-surface tests.

Run:
    python -m app.write_surface.tests.test_get_or_create_customer_pg
"""

from __future__ import annotations

from contextlib import contextmanager
from uuid import UUID, uuid4

from sqlalchemy import event, select
from sqlmodel import Session

from app.core.database import engine
from app.core.phone import normalize_phone_value
from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import UserRole
from app.models.prepared_action import STATUS_CONSUMED, PreparedAction
from app.models.user import User
from app.write_surface.engine import ErrorCode, WriteSurfaceError, commit, prepare


# --- Savepoint-rollback session (duplicated from sibling test modules) -------

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


def _seed(session: Session, bid: UUID) -> User:
    session.add(Business(id=bid, name="GC Test Co", phone="9990000000"))
    session.flush()
    user = User(
        id=uuid4(), business_id=bid, name="Owner",
        email=f"owner-{bid}@test.local", role=UserRole.OWNER, is_active=True,
    )
    session.add(user)
    session.commit()
    return user


def _seed_existing_customer(session: Session, bid: UUID, *, phone: str, name: str) -> UUID:
    cid = uuid4()
    session.add(Customer(
        id=cid, business_id=bid, name=name,
        phone=phone, phone_normalized=normalize_phone_value(phone) or phone,
    ))
    session.commit()
    return cid


# ---------------------------------------------------------------------------
# Existing-customer branch
# ---------------------------------------------------------------------------

def test_prepare_existing_customer_preview_says_existing():
    with _rollback_session() as s:
        bid = uuid4()
        user = _seed(s, bid)
        _seed_existing_customer(s, bid, phone="+91 99999 12345", name="Rajesh Mehta")
        h = prepare(s, user, capability_name="get_or_create_customer", raw_inputs={
            "phone": "+91 99999 12345",
            "name": "Rajesh Mehta (typo doesn't matter)",
        })
        assert "Existing customer" in h.preview
        assert "Rajesh Mehta" in h.preview      # the existing name wins in preview


def test_commit_existing_returns_created_false_and_no_new_row():
    with _rollback_session() as s:
        bid = uuid4()
        user = _seed(s, bid)
        existing_id = _seed_existing_customer(s, bid, phone="+91 99999 22222", name="Anjali")

        h = prepare(s, user, capability_name="get_or_create_customer", raw_inputs={
            "phone": "+91 99999 22222", "name": "Anjali",
        })
        result = commit(s, user, id=h.id)
        assert result == {"customer_id": str(existing_id), "created": False}

        # Still exactly one customer row with this phone.
        rows = s.execute(select(Customer).where(Customer.business_id == bid)).all()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# New-customer branch
# ---------------------------------------------------------------------------

def test_prepare_new_customer_preview_says_new():
    with _rollback_session() as s:
        bid = uuid4()
        user = _seed(s, bid)
        h = prepare(s, user, capability_name="get_or_create_customer", raw_inputs={
            "phone": "+91 99999 33333", "name": "Brand New",
        })
        assert "New customer will be created" in h.preview
        assert "Brand New" in h.preview


def test_commit_new_creates_row_and_returns_created_true():
    with _rollback_session() as s:
        bid = uuid4()
        user = _seed(s, bid)
        h = prepare(s, user, capability_name="get_or_create_customer", raw_inputs={
            "phone": "+91 99999 44444", "name": "Deepak Joshi",
        })
        result = commit(s, user, id=h.id)
        assert result["created"] is True
        assert UUID(result["customer_id"])  # parses

        row = s.get(Customer, UUID(result["customer_id"]))
        assert row.business_id == bid
        assert row.name == "Deepak Joshi"
        assert row.phone == "+91 99999 44444"
        assert row.phone_normalized   # set by the service


# ---------------------------------------------------------------------------
# Single-use + editable-field flow
# ---------------------------------------------------------------------------

def test_second_commit_rejected():
    with _rollback_session() as s:
        bid = uuid4()
        user = _seed(s, bid)
        h = prepare(s, user, capability_name="get_or_create_customer", raw_inputs={
            "phone": "+91 99999 55555", "name": "Once",
        })
        commit(s, user, id=h.id)
        try:
            commit(s, user, id=h.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")
        s.expire_all()
        assert s.get(PreparedAction, h.id).status == STATUS_CONSUMED


def test_editing_notes_flows_through_on_create():
    """For the NEW branch, the edited `notes` lands on the created Customer row."""
    with _rollback_session() as s:
        bid = uuid4()
        user = _seed(s, bid)
        h = prepare(s, user, capability_name="get_or_create_customer", raw_inputs={
            "phone": "+91 99999 66666", "name": "Notes Test",
            "notes": "draft note from LLM",
        })
        result = commit(s, user, id=h.id, edited_fields={
            "notes": "the human rewrote this before clicking confirm",
        })
        row = s.get(Customer, UUID(result["customer_id"]))
        assert row.notes == "the human rewrote this before clicking confirm"


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
