"""Postgres-backed integration tests for the prepared-action store.

Resolves a real tension that the store's design imposes on tests:

  The store COMMITS inside both ``create_prepared_action`` and
  ``consume_prepared_action`` — commit is REQUIRED for the atomic single-use
  guarantee to hold under concurrency (two sessions in two transactions only see
  each other's row changes after commit, and the row lock is only released at
  commit). But our integration-test style relies on a final ``rollback()`` to
  leave zero rows behind.

We resolve it two ways, on purpose:

  1) For tests where ONE session is enough, we use the canonical SQLAlchemy
     "outer transaction + nested SAVEPOINT" pattern (``_rollback_session()``).
     The store's ``session.commit()`` only commits the SAVEPOINT; the outer
     transaction is rolled back when the context exits, so the test leaves
     ZERO rows. This is the right pattern for asserting store semantics.

  2) For the CONCURRENCY test, two threads need two real, independent
     transactions on the same engine — and they only see each other's writes
     via real commits. The savepoint trick won't work there because both
     threads must share a base row that's actually visible across connections.
     That test commits to the real DB and cleans up its own rows in ``finally``
     (small, contained leakage on test crash; zero in the happy path).

Run:
    python -m app.write_surface.tests.test_prepared_actions_pg
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Thread
from uuid import uuid4

from sqlalchemy import delete, event
from sqlmodel import Session

from app.core.database import engine
from app.models.business import Business
from app.models.prepared_action import STATUS_CONSUMED, STATUS_PENDING, PreparedAction
from app.write_surface.prepared_actions import (
    PREPARED_ACTION_TTL,
    PreparedActionAlreadyConsumed,
    PreparedActionExpired,
    PreparedActionNotFound,
    PreparedActionWrongBusiness,
    cleanup_old_prepared_actions,
    consume_prepared_action,
    create_prepared_action,
)


# ---------------------------------------------------------------------------
# Outer-transaction + SAVEPOINT helper — see module docstring.
# Each test runs against a real Postgres connection, but every commit the store
# makes during the test only commits the SAVEPOINT; the outer transaction
# rolls back at context exit, leaving the DB unchanged.
# ---------------------------------------------------------------------------

@contextmanager
def _rollback_session():
    connection = engine.connect()
    outer_tx = connection.begin()
    session = Session(bind=connection)
    session.begin_nested()  # SAVEPOINT — store commits land here, not the outer tx

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, transaction):
        # Whenever the savepoint ends (the store called commit), reopen one so
        # subsequent operations in the test stay inside a nested transaction.
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    try:
        yield session
    finally:
        event.remove(session, "after_transaction_end", _restart_savepoint)
        session.close()
        outer_tx.rollback()
        connection.close()


def _seed_business(session, bid):
    """Insert a minimal Business row so prepared_actions FK is satisfied. The
    insert is committed; within the savepoint pattern that commit lands on the
    SAVEPOINT and is rolled back at end of test. In the concurrency test the
    caller is responsible for cleanup."""
    session.add(Business(id=bid, name="PA Test Co", phone="9990000000"))
    session.commit()


def _make_pending(session, *, business_id, capability="test.write") -> str:
    return create_prepared_action(
        session,
        business_id=business_id,
        capability=capability,
        locked_data={"x": 1},
        editable_data={"y": 2},
        preview="do the thing",
    )


# ---------------------------------------------------------------------------
# Happy path + identity / shape checks
# ---------------------------------------------------------------------------

def test_create_returns_opaque_token_and_persists_row():
    with _rollback_session() as s:
        bid = uuid4()
        _seed_business(s, bid)
        token = _make_pending(s, business_id=bid)
        assert isinstance(token, str) and len(token) >= 40, token
        row = s.get(PreparedAction, token)
        assert row is not None
        assert row.status == STATUS_PENDING
        assert row.business_id == bid
        assert row.capability == "test.write"
        # TTL applied correctly (give a small tolerance for clock skew).
        delta = row.expires_at - row.created_at
        assert abs((delta - PREPARED_ACTION_TTL).total_seconds()) < 1.0


def test_create_then_consume_succeeds():
    with _rollback_session() as s:
        bid = uuid4()
        _seed_business(s, bid)
        token = _make_pending(s, business_id=bid)
        row = consume_prepared_action(s, id=token, business_id=bid)
        assert row.status == STATUS_CONSUMED
        assert row.id == token
        assert row.business_id == bid
        # Locked + editable payloads round-trip intact.
        assert row.locked_data == {"x": 1}
        assert row.editable_data == {"y": 2}


# ---------------------------------------------------------------------------
# Each rejection — distinct exception subclass
# ---------------------------------------------------------------------------

def test_consume_unknown_id_rejected_not_found():
    with _rollback_session() as s:
        try:
            consume_prepared_action(s, id="no-such-token", business_id=uuid4())
        except PreparedActionNotFound:
            pass
        else:
            raise AssertionError("expected PreparedActionNotFound")


def test_second_consume_rejected_already_consumed():
    with _rollback_session() as s:
        bid = uuid4()
        _seed_business(s, bid)
        token = _make_pending(s, business_id=bid)
        consume_prepared_action(s, id=token, business_id=bid)  # first OK
        try:
            consume_prepared_action(s, id=token, business_id=bid)
        except PreparedActionAlreadyConsumed:
            pass
        else:
            raise AssertionError("expected PreparedActionAlreadyConsumed on second consume")
        # And the row is still consumed (no mutation by the second attempt).
        assert s.get(PreparedAction, token).status == STATUS_CONSUMED


def test_consume_wrong_business_rejected_and_row_not_flipped():
    with _rollback_session() as s:
        bid_owner = uuid4()
        bid_other = uuid4()
        _seed_business(s, bid_owner)
        # bid_other is never used to insert anything (consume reads/updates only),
        # so it doesn't need a real Business row.
        token = _make_pending(s, business_id=bid_owner)
        try:
            consume_prepared_action(s, id=token, business_id=bid_other)
        except PreparedActionWrongBusiness:
            pass
        else:
            raise AssertionError("expected PreparedActionWrongBusiness")
        # Critical: the row MUST still be pending. If business_id weren't in the
        # WHERE of the conditional UPDATE, the row would have been flipped to
        # consumed before the post-check raised — that is the bug this test guards.
        s.expire_all()  # drop any cached state, re-read from DB
        row = s.get(PreparedAction, token)
        assert row.status == STATUS_PENDING, "row was flipped despite wrong tenant"


def test_consume_expired_rejected_and_row_not_flipped():
    # We insert a row directly with a past expires_at (bypassing the store's TTL
    # math) — same pattern the audit tests used for fixture-only state.
    with _rollback_session() as s:
        bid = uuid4()
        _seed_business(s, bid)
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        row = PreparedAction(
            id="expired-token-fixture-" + uuid4().hex,
            business_id=bid,
            capability="test.expired",
            locked_data={}, editable_data={},
            preview="already-expired",
            status=STATUS_PENDING,
            created_at=past - PREPARED_ACTION_TTL,
            expires_at=past,
        )
        s.add(row)
        s.commit()
        try:
            consume_prepared_action(s, id=row.id, business_id=bid)
        except PreparedActionExpired:
            pass
        else:
            raise AssertionError("expected PreparedActionExpired")
        # The row MUST still be pending — expired rows must never be flipped.
        s.expire_all()
        assert s.get(PreparedAction, row.id).status == STATUS_PENDING, "expired row was flipped"


# ---------------------------------------------------------------------------
# Atomic single-use — single-threaded assertion
# ---------------------------------------------------------------------------

def test_atomic_flip_changes_nothing_on_second_attempt():
    with _rollback_session() as s:
        bid = uuid4()
        _seed_business(s, bid)
        token = _make_pending(s, business_id=bid)
        first = consume_prepared_action(s, id=token, business_id=bid)
        assert first.status == STATUS_CONSUMED
        # Capture immutable identity to prove the row isn't replaced or mutated.
        original_created = first.created_at
        original_locked = dict(first.locked_data)

        # Second attempt: must raise, AND must not touch the row.
        try:
            consume_prepared_action(s, id=token, business_id=bid)
        except PreparedActionAlreadyConsumed:
            pass
        else:
            raise AssertionError("expected PreparedActionAlreadyConsumed")

        s.expire_all()
        row = s.get(PreparedAction, token)
        assert row.status == STATUS_CONSUMED
        assert row.created_at == original_created
        assert row.locked_data == original_locked


# ---------------------------------------------------------------------------
# REAL concurrency — two threads, two sessions, two transactions on one row.
# Cannot use the savepoint pattern (threads must SEE each other via real commits).
# Cleans up its own rows in finally; small, contained leakage on test crash only.
# ---------------------------------------------------------------------------

def test_concurrent_consume_exactly_one_wins():
    bid = uuid4()
    # Set-up: insert + commit a real Business (needed for the FK) and a pending
    # prepared row so both threads can read it. Both are cleaned up in finally.
    with Session(engine) as setup:
        _seed_business(setup, bid)
        token = _make_pending(setup, business_id=bid)

    try:
        results: list[tuple[str, str | None]] = []

        def worker():
            # Each thread has its OWN session + transaction; this is what makes
            # the race real. Postgres serializes the conditional UPDATE on the
            # same row via its row lock; only one rowcount==1 is possible.
            with Session(engine) as ts:
                try:
                    row = consume_prepared_action(ts, id=token, business_id=bid)
                    results.append(("ok", row.id))
                except PreparedActionAlreadyConsumed:
                    results.append(("already_consumed", None))
                except Exception as exc:  # noqa: BLE001 — surface anything unexpected in the assert
                    results.append(("error", repr(exc)))

        t1, t2 = Thread(target=worker), Thread(target=worker)
        t1.start(); t2.start()
        t1.join(); t2.join()

        outcomes = sorted(r[0] for r in results)
        assert outcomes == ["already_consumed", "ok"], f"expected exactly one winner, got {results!r}"

        # And the persisted row is consumed exactly once.
        with Session(engine) as check:
            row = check.get(PreparedAction, token)
            assert row is not None and row.status == STATUS_CONSUMED
    finally:
        # Explicit cleanup (we committed outside the savepoint pattern).
        with Session(engine) as cleanup:
            cleanup.execute(delete(PreparedAction).where(PreparedAction.id == token))
            cleanup.execute(delete(Business).where(Business.id == bid))
            cleanup.commit()


# ---------------------------------------------------------------------------
# Cleanup function
# ---------------------------------------------------------------------------

def test_cleanup_deletes_old_rows_only():
    with _rollback_session() as s:
        bid = uuid4()
        _seed_business(s, bid)
        now = datetime.now(timezone.utc)

        # An OLD consumed row (created 2 days ago).
        old = PreparedAction(
            id="old-" + uuid4().hex, business_id=bid, capability="t",
            locked_data={}, editable_data={}, preview="old",
            status=STATUS_CONSUMED,
            created_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=2) + PREPARED_ACTION_TTL,
        )
        # A fresh pending row.
        fresh_token = _make_pending(s, business_id=bid)
        s.add(old)
        s.commit()
        old_id = old.id  # capture before deletion (avoid stale-ORM-attribute access)

        deleted = cleanup_old_prepared_actions(s, older_than=timedelta(days=1))
        assert deleted >= 1
        s.expire_all()
        assert s.get(PreparedAction, old_id) is None, "old row should be deleted"
        assert s.get(PreparedAction, fresh_token) is not None, "fresh row should remain"


# ---------------------------------------------------------------------------
# Runner (skips cleanly if DB unreachable)
# ---------------------------------------------------------------------------

def _run() -> int:
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP  database unreachable ({type(exc).__name__}: {str(exc)[:120]}) — integration tests skipped")
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
