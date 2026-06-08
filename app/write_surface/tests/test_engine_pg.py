"""Postgres-backed tests for the write-surface engine + record_payment capability.

Runs against a real Postgres. Uses the savepoint-rollback pattern (duplicated
from test_prepared_actions_pg.py — keep the two helpers in sync if changing the
pattern). The store + payment_service both COMMIT internally; with the outer
transaction + nested savepoint, those commits land on the savepoint and are
rolled back at the end of each test.

Run:
    python -m app.write_surface.tests.test_engine_pg
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import event, select
from sqlmodel import Session

from app.core.database import engine
from app.models.business import Business
from app.models.enums import InvoiceStatus, PaymentMethod, UserRole
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.prepared_action import STATUS_CONSUMED, STATUS_PENDING, PreparedAction
from app.models.user import User
from app.write_surface.engine import ErrorCode, WriteSurfaceError, commit, prepare
from app.write_surface.prepared_actions import PREPARED_ACTION_TTL


# --- Savepoint-rollback session (see module docstring) -----------------------

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


# --- Fixture: business + user + APPROVED invoice (total 100000, lead_id None) ---

def _seed_business_and_invoice(
    session: Session,
    bid,
    *,
    total: Decimal = Decimal("100000.00"),
    status: InvoiceStatus = InvoiceStatus.APPROVED,
    number: str = "WS-001",
) -> tuple[User, UUID]:
    session.add(Business(id=bid, name="WS Test Co", phone="9990000000"))
    session.flush()

    user = User(
        id=uuid4(), business_id=bid,
        name="Owner", email=f"owner-{bid}@test.local",
        role=UserRole.OWNER, is_active=True,
    )
    session.add(user)
    session.flush()

    inv_id = uuid4()
    session.add(Invoice(
        id=inv_id, business_id=bid,
        status=status,
        issued_date=date(2026, 5, 1),
        due_date=date(2026, 6, 1),
        subtotal=total, tax_total=Decimal("0.00"), total_amount=total,
        invoice_number=number,
    ))
    session.commit()
    return user, inv_id


# ---------------------------------------------------------------------------
# Spec list (1-9 from the task) + 1 extra (missing invoice rejected at prepare).
# ---------------------------------------------------------------------------

def test_prepare_returns_id_and_preview():
    with _rollback_session() as s:
        bid = uuid4()
        user, inv_id = _seed_business_and_invoice(s, bid)
        handle = prepare(
            s, user,
            capability_name="record_payment",
            raw_inputs={"invoice_id": str(inv_id), "amount": "30000.00", "payment_method": "upi"},
        )
        assert isinstance(handle.id, str) and len(handle.id) > 30
        assert "WS-001" in handle.preview
        assert "30,000.00" in handle.preview
        assert "current balance" in handle.preview and "new balance" in handle.preview
        assert sorted(handle.editable_fields) == ["payment_date", "reference"]


def test_commit_creates_payment_row():
    with _rollback_session() as s:
        bid = uuid4()
        user, inv_id = _seed_business_and_invoice(s, bid)
        handle = prepare(
            s, user,
            capability_name="record_payment",
            raw_inputs={"invoice_id": str(inv_id), "amount": "30000.00", "payment_method": "upi"},
        )
        result = commit(s, user, id=handle.id)
        assert "payment_id" in result

        rows = s.execute(select(Payment).where(Payment.invoice_id == inv_id)).all()
        assert len(rows) == 1
        payment = rows[0][0]
        assert payment.business_id == bid
        assert payment.amount == Decimal("30000.00")
        assert payment.payment_method == PaymentMethod.UPI

        # And the prepared action is now consumed.
        s.expire_all()
        assert s.get(PreparedAction, handle.id).status == STATUS_CONSUMED


def test_second_commit_rejected_already_consumed():
    with _rollback_session() as s:
        bid = uuid4()
        user, inv_id = _seed_business_and_invoice(s, bid)
        handle = prepare(
            s, user,
            capability_name="record_payment",
            raw_inputs={"invoice_id": str(inv_id), "amount": "100.00", "payment_method": "cash"},
        )
        commit(s, user, id=handle.id)
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_expired_prepared_action_cannot_be_committed():
    with _rollback_session() as s:
        bid = uuid4()
        user, inv_id = _seed_business_and_invoice(s, bid)

        # Insert a prepared_action row directly with a past expires_at —
        # bypasses the store's TTL math (same fixture pattern as the store tests).
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        row = PreparedAction(
            id="expired-" + uuid4().hex,
            business_id=bid,
            capability="record_payment",
            locked_data={
                "invoice_id": str(inv_id),
                "amount": "100.00",
                "payment_method": "cash",
            },
            editable_data={},
            preview="expired-fixture",
            status=STATUS_PENDING,
            created_at=past - PREPARED_ACTION_TTL,
            expires_at=past,
        )
        s.add(row); s.commit()

        try:
            commit(s, user, id=row.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE for expired action")


def test_editing_editable_field_works():
    with _rollback_session() as s:
        bid = uuid4()
        user, inv_id = _seed_business_and_invoice(s, bid)
        handle = prepare(
            s, user,
            capability_name="record_payment",
            raw_inputs={"invoice_id": str(inv_id), "amount": "5000.00", "payment_method": "bank_transfer"},
        )
        result = commit(s, user, id=handle.id, edited_fields={
            "payment_date": "2026-05-22",
            "reference": "UTR-12345",
        })
        payment = s.execute(select(Payment).where(Payment.id == UUID(result["payment_id"]))).first()[0]
        assert payment.payment_date == date(2026, 5, 22)
        assert payment.reference == "UTR-12345"


def test_editing_locked_field_rejected():
    with _rollback_session() as s:
        bid = uuid4()
        user, inv_id = _seed_business_and_invoice(s, bid)
        handle = prepare(
            s, user,
            capability_name="record_payment",
            raw_inputs={"invoice_id": str(inv_id), "amount": "5000.00", "payment_method": "upi"},
        )
        try:
            commit(s, user, id=handle.id, edited_fields={"amount": "999.00"})
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "amount" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT when editing locked field")
        # The action is now consumed (atomic claim ran before edited_fields validation).
        s.expire_all()
        assert s.get(PreparedAction, handle.id).status == STATUS_CONSUMED


def test_execute_failure_returns_clean_error_not_500():
    """Cancel the invoice between prepare and commit -> payment_service raises ->
    engine returns EXECUTE_FAILED (NOT a 500) and the action stays consumed."""
    with _rollback_session() as s:
        bid = uuid4()
        user, inv_id = _seed_business_and_invoice(s, bid)
        handle = prepare(
            s, user,
            capability_name="record_payment",
            raw_inputs={"invoice_id": str(inv_id), "amount": "1000.00", "payment_method": "upi"},
        )

        # Cancel between prepare and commit.
        inv = s.get(Invoice, inv_id)
        inv.status = InvoiceStatus.CANCELLED
        s.add(inv); s.commit()

        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.EXECUTE_FAILED
            assert "prepare again" in exc.message
        else:
            raise AssertionError("expected EXECUTE_FAILED when the service rejects")

        s.expire_all()
        assert s.get(PreparedAction, handle.id).status == STATUS_CONSUMED
        # And NO payment row was created.
        assert s.execute(select(Payment).where(Payment.invoice_id == inv_id)).all() == []


def test_unknown_capability_rejected():
    with _rollback_session() as s:
        bid = uuid4()
        user, _ = _seed_business_and_invoice(s, bid)
        try:
            prepare(s, user, capability_name="not_a_real_capability", raw_inputs={})
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.UNKNOWN_CAPABILITY
        else:
            raise AssertionError("expected UNKNOWN_CAPABILITY")


def test_invalid_input_rejected_at_prepare_bad_enum():
    with _rollback_session() as s:
        bid = uuid4()
        user, inv_id = _seed_business_and_invoice(s, bid)
        try:
            prepare(
                s, user,
                capability_name="record_payment",
                raw_inputs={"invoice_id": str(inv_id), "amount": "100.00", "payment_method": "crypto"},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "payment_method" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for bad enum")


def test_invalid_input_rejected_at_prepare_missing_invoice():
    """The validate hook rejects when the invoice doesn't exist for this business."""
    with _rollback_session() as s:
        bid = uuid4()
        user, _ = _seed_business_and_invoice(s, bid)
        try:
            prepare(
                s, user,
                capability_name="record_payment",
                raw_inputs={"invoice_id": str(uuid4()), "amount": "100.00", "payment_method": "upi"},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "not found" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for missing invoice")


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
