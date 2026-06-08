"""Postgres-backed tests for the three payment-correction capabilities:
void_payment, update_payment_metadata, update_payment_amount.

Same shape as the other write-surface PG tests: savepoint-rollback, real
Postgres, prepare -> commit. Per cap covers the standard spec battery, plus:

  * void_payment:           invoice balance restored by the voided amount
  * update_payment_metadata: omitted field NOT overwritten (selective patch)
  * update_payment_amount:  the void+replace audit chain is correct
                            (old.voided_at set, new.replaces_payment_id linked,
                             invoice balance reflects the corrected amount)

Run:
    python -m app.write_surface.tests.test_payment_caps_pg
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlmodel import Session, select

from app.core.database import engine
from app.core.phone import normalize_phone_value
from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import (
    InvoiceStatus,
    LeadSource,
    PaymentMethod,
    UserRole,
)
from app.models.invoice import Invoice
from app.models.lead import Lead
from app.models.payment import Payment
from app.models.pipeline import Pipeline, PipelineStage
from app.models.prepared_action import STATUS_CONSUMED, PreparedAction
from app.models.user import User
from app.write_surface.engine import ErrorCode, WriteSurfaceError, commit, prepare


# --- Savepoint-rollback session ---------------------------------------------

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


# --- Fixture: business + user + lead + APPROVED invoice + one payment -------

def _d(v) -> Decimal:
    return Decimal(str(v))


def _seed_invoice_with_payment(
    session: Session, *,
    label: str = "A",
    invoice_total: Decimal = Decimal("10000.00"),
    payment_amount: Decimal = Decimal("3000.00"),
    payment_method: PaymentMethod = PaymentMethod.UPI,
) -> tuple[User, UUID, UUID]:
    """Returns (user, invoice_id, payment_id). Invoice APPROVED. After the
    seeded payment, balance = 10000 - 3000 = 7000."""
    bid = uuid4()
    session.add(Business(id=bid, name=f"Pay Test {label}",
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
    session.add(Customer(
        id=cust_id, business_id=bid,
        name=f"Cust {label}",
        phone=f"+91 9999900{label}",
        phone_normalized=normalize_phone_value(f"+91 9999900{label}"),
    ))
    session.flush()
    lead_id = uuid4()
    session.add(Lead(
        id=lead_id, business_id=bid, customer_id=cust_id, stage_id=sid,
        title=f"Lead {label}", source=LeadSource.WHATSAPP,
    ))
    session.flush()
    inv_id = uuid4()
    session.add(Invoice(
        id=inv_id, business_id=bid, lead_id=lead_id,
        status=InvoiceStatus.APPROVED,
        issued_date=date(2026, 5, 1),
        due_date=date(2026, 6, 1),
        subtotal=invoice_total, tax_total=_d(0), total_amount=invoice_total,
        invoice_number=f"INV-{label}",
    ))
    session.flush()
    pay_id = uuid4()
    session.add(Payment(
        id=pay_id, business_id=bid, invoice_id=inv_id,
        amount=payment_amount, payment_method=payment_method,
        payment_date=date(2026, 5, 10),
        reference="orig-ref",
    ))
    session.commit()
    return user, inv_id, pay_id


def _active_paid_for(session: Session, invoice_id: UUID) -> Decimal:
    """Sum of active (non-voided) payments on the invoice."""
    rows = session.exec(
        select(Payment).where(
            Payment.invoice_id == invoice_id,
            Payment.voided_at.is_(None),
        )
    ).all()
    return sum((_d(p.amount) for p in rows), _d(0))


# ===========================================================================
# void_payment
# ===========================================================================

def test_void_payment_prepare_shows_balance_restoration():
    with _rollback_session() as s:
        user, inv_id, pay_id = _seed_invoice_with_payment(s)
        handle = prepare(
            s, user, capability_name="void_payment",
            raw_inputs={"payment_id": str(pay_id), "reason": "duplicate entry"},
        )
        assert isinstance(handle.id, str) and len(handle.id) > 30
        # Preview must show: amount + method + invoice + balance shift up.
        assert "3,000.00" in handle.preview
        assert "upi" in handle.preview
        assert "INV-A" in handle.preview
        assert "7,000.00" in handle.preview     # current balance (10000 - 3000)
        assert "10,000.00" in handle.preview    # new balance after void
        assert "duplicate entry" in handle.preview
        assert list(handle.editable_fields) == ["reason"]


def test_void_payment_commit_voids_and_restores_balance():
    with _rollback_session() as s:
        user, inv_id, pay_id = _seed_invoice_with_payment(s)
        handle = prepare(
            s, user, capability_name="void_payment",
            raw_inputs={"payment_id": str(pay_id)},
        )
        result = commit(s, user, id=handle.id)
        assert "voided_at" in result and result["voided_at"] is not None

        s.expire_all()
        row = s.get(Payment, pay_id)
        assert row.voided_at is not None
        assert row.voided_by == user.id
        # Active-payments sum on this invoice is now 0; balance restored to total.
        assert _active_paid_for(s, inv_id) == _d(0)
        # Action consumed.
        assert s.get(PreparedAction, handle.id).status == STATUS_CONSUMED


def test_void_payment_second_commit_rejected():
    with _rollback_session() as s:
        user, _inv_id, pay_id = _seed_invoice_with_payment(s)
        handle = prepare(
            s, user, capability_name="void_payment",
            raw_inputs={"payment_id": str(pay_id)},
        )
        commit(s, user, id=handle.id)
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_void_payment_edit_reason_flows_through():
    with _rollback_session() as s:
        user, _inv_id, pay_id = _seed_invoice_with_payment(s)
        handle = prepare(
            s, user, capability_name="void_payment",
            raw_inputs={"payment_id": str(pay_id), "reason": "agent reason"},
        )
        commit(s, user, id=handle.id, edited_fields={"reason": "human edited"})
        s.expire_all()
        assert s.get(Payment, pay_id).voided_reason == "human edited"


def test_void_payment_edit_locked_field_rejected():
    with _rollback_session() as s:
        user, _inv_id, pay_id = _seed_invoice_with_payment(s)
        handle = prepare(
            s, user, capability_name="void_payment",
            raw_inputs={"payment_id": str(pay_id)},
        )
        try:
            commit(s, user, id=handle.id, edited_fields={"payment_id": str(uuid4())})
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
        else:
            raise AssertionError("expected INVALID_INPUT for locked-field edit")


def test_void_payment_cross_tenant_rejected():
    with _rollback_session() as s:
        _user_a, _inv_a, pay_a = _seed_invoice_with_payment(s, label="A")
        user_b, _inv_b, _pay_b = _seed_invoice_with_payment(s, label="B")
        try:
            prepare(
                s, user_b, capability_name="void_payment",
                raw_inputs={"payment_id": str(pay_a)},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "not found" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT cross-tenant")


def test_void_payment_already_voided_rejected_at_prepare():
    with _rollback_session() as s:
        user, _inv_id, pay_id = _seed_invoice_with_payment(s)
        # Pre-void it.
        pay = s.get(Payment, pay_id)
        from datetime import datetime, timezone
        pay.voided_at = datetime.now(timezone.utc)
        pay.voided_reason = "test pre-void"
        pay.voided_by = user.id
        s.add(pay); s.commit()
        try:
            prepare(
                s, user, capability_name="void_payment",
                raw_inputs={"payment_id": str(pay_id)},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "already voided" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for already-voided")


# ===========================================================================
# update_payment_metadata
# ===========================================================================

def test_update_payment_metadata_prepare_shows_old_to_new():
    with _rollback_session() as s:
        user, _inv_id, pay_id = _seed_invoice_with_payment(s)
        handle = prepare(
            s, user, capability_name="update_payment_metadata",
            raw_inputs={
                "payment_id": str(pay_id),
                "payment_date": "2026-05-15",
                "reference": "new-ref-XYZ",
            },
        )
        assert "INV-A" in handle.preview
        assert "amount unchanged" in handle.preview
        assert "payment_date: 2026-05-10 → 2026-05-15" in handle.preview
        assert "reference: orig-ref → new-ref-XYZ" in handle.preview
        assert sorted(handle.editable_fields) == [
            "payment_date", "payment_method", "reference",
        ]


def test_update_payment_metadata_selective_patch_leaves_unmentioned_fields():
    """Mandatory selective-patch guard: patching only the reference leaves
    payment_date and payment_method at their seeded values."""
    with _rollback_session() as s:
        user, _inv_id, pay_id = _seed_invoice_with_payment(s)
        handle = prepare(
            s, user, capability_name="update_payment_metadata",
            raw_inputs={"payment_id": str(pay_id), "reference": "patched-only"},
        )
        commit(s, user, id=handle.id)
        s.expire_all()
        row = s.get(Payment, pay_id)
        assert row.reference == "patched-only"
        # Untouched:
        assert row.payment_date == date(2026, 5, 10)
        assert row.payment_method == PaymentMethod.UPI


def test_update_payment_metadata_commit_amount_unchanged_balance_unchanged():
    with _rollback_session() as s:
        user, inv_id, pay_id = _seed_invoice_with_payment(s)
        balance_before = _active_paid_for(s, inv_id)
        handle = prepare(
            s, user, capability_name="update_payment_metadata",
            raw_inputs={"payment_id": str(pay_id), "payment_method": "cash"},
        )
        commit(s, user, id=handle.id)
        s.expire_all()
        # Method changed but amount + balance untouched.
        assert s.get(Payment, pay_id).payment_method == PaymentMethod.CASH
        assert _active_paid_for(s, inv_id) == balance_before


def test_update_payment_metadata_second_commit_rejected():
    with _rollback_session() as s:
        user, _inv_id, pay_id = _seed_invoice_with_payment(s)
        handle = prepare(
            s, user, capability_name="update_payment_metadata",
            raw_inputs={"payment_id": str(pay_id), "reference": "x"},
        )
        commit(s, user, id=handle.id)
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_update_payment_metadata_edit_field_flows_through():
    with _rollback_session() as s:
        user, _inv_id, pay_id = _seed_invoice_with_payment(s)
        handle = prepare(
            s, user, capability_name="update_payment_metadata",
            raw_inputs={"payment_id": str(pay_id), "reference": "agent-ref"},
        )
        commit(s, user, id=handle.id, edited_fields={"reference": "human-ref"})
        s.expire_all()
        assert s.get(Payment, pay_id).reference == "human-ref"


def test_update_payment_metadata_edit_locked_field_rejected():
    with _rollback_session() as s:
        user, _inv_id, pay_id = _seed_invoice_with_payment(s)
        handle = prepare(
            s, user, capability_name="update_payment_metadata",
            raw_inputs={"payment_id": str(pay_id), "reference": "x"},
        )
        try:
            commit(s, user, id=handle.id, edited_fields={"payment_id": str(uuid4())})
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
        else:
            raise AssertionError("expected INVALID_INPUT for locked-field edit")


def test_update_payment_metadata_cross_tenant_rejected():
    with _rollback_session() as s:
        _user_a, _, pay_a = _seed_invoice_with_payment(s, label="A")
        user_b, _, _pay_b = _seed_invoice_with_payment(s, label="B")
        try:
            prepare(
                s, user_b, capability_name="update_payment_metadata",
                raw_inputs={"payment_id": str(pay_a), "reference": "x"},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "not found" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT cross-tenant")


def test_update_payment_metadata_no_editable_fields_rejected():
    with _rollback_session() as s:
        user, _inv_id, pay_id = _seed_invoice_with_payment(s)
        try:
            prepare(
                s, user, capability_name="update_payment_metadata",
                raw_inputs={"payment_id": str(pay_id)},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "no fields to update" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for empty patch")


# ===========================================================================
# update_payment_amount  (the careful one — void+replace)
# ===========================================================================

def test_update_payment_amount_prepare_shows_void_and_replace_explicitly():
    """The headline preview: the user must SEE that this is a void+replace
    and the balance impact, not a silent in-place edit."""
    with _rollback_session() as s:
        user, _inv_id, pay_id = _seed_invoice_with_payment(s)
        handle = prepare(
            s, user, capability_name="update_payment_amount",
            raw_inputs={
                "payment_id": str(pay_id),
                "amount": "5000.00",
                "reason": "miscounted",
            },
        )
        # Preview must be honest about void+replace:
        assert "CORRECT payment amount" in handle.preview
        assert "void the current ₹3,000.00" in handle.preview
        assert "new payment of ₹5,000.00" in handle.preview
        assert "replaces_payment_id" in handle.preview
        assert "INV-A" in handle.preview
        # Balance impact: 7000 → 5000 (net 2000 reduction).
        assert "₹7,000.00 → ₹5,000.00" in handle.preview
        assert "miscounted" in handle.preview
        assert sorted(handle.editable_fields) == ["amount", "reason"]


def test_update_payment_amount_commit_void_and_replace_audit_chain():
    """The audit chain: old.voided_at + voided_by + voided_reason are set;
    a NEW payment is created with replaces_payment_id linked to the old;
    invoice balance reflects the corrected amount."""
    with _rollback_session() as s:
        user, inv_id, old_pay_id = _seed_invoice_with_payment(s)
        handle = prepare(
            s, user, capability_name="update_payment_amount",
            raw_inputs={
                "payment_id": str(old_pay_id),
                "amount": "5000.00",
                "reason": "off-by-2k",
            },
        )
        result = commit(s, user, id=handle.id)

        # Service returned the NEW payment.
        assert "new_payment_id" in result
        assert result["replaces_payment_id"] == str(old_pay_id)
        new_pay_id = UUID(result["new_payment_id"])
        assert new_pay_id != old_pay_id

        s.expire_all()
        old = s.get(Payment, old_pay_id)
        new = s.get(Payment, new_pay_id)
        # Old: voided correctly.
        assert old.voided_at is not None
        assert old.voided_by == user.id
        assert "off-by-2k" in (old.voided_reason or "")
        # New: live, linked to old.
        assert new.voided_at is None
        assert new.replaces_payment_id == old_pay_id
        assert new.amount == _d("5000.00")
        # Old metadata carried over.
        assert new.payment_method == old.payment_method
        assert new.payment_date == old.payment_date
        # Invoice balance: only the new payment is active.
        assert _active_paid_for(s, inv_id) == _d("5000.00")


def test_update_payment_amount_second_commit_rejected():
    with _rollback_session() as s:
        user, _inv_id, pay_id = _seed_invoice_with_payment(s)
        handle = prepare(
            s, user, capability_name="update_payment_amount",
            raw_inputs={"payment_id": str(pay_id), "amount": "5000.00"},
        )
        commit(s, user, id=handle.id)
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_update_payment_amount_edit_amount_flows_through():
    with _rollback_session() as s:
        user, inv_id, pay_id = _seed_invoice_with_payment(s)
        handle = prepare(
            s, user, capability_name="update_payment_amount",
            raw_inputs={"payment_id": str(pay_id), "amount": "5000.00"},
        )
        result = commit(s, user, id=handle.id, edited_fields={"amount": "4000.00"})
        s.expire_all()
        new = s.get(Payment, UUID(result["new_payment_id"]))
        assert new.amount == _d("4000.00")
        assert _active_paid_for(s, inv_id) == _d("4000.00")


def test_update_payment_amount_edit_locked_field_rejected():
    with _rollback_session() as s:
        user, _inv_id, pay_id = _seed_invoice_with_payment(s)
        handle = prepare(
            s, user, capability_name="update_payment_amount",
            raw_inputs={"payment_id": str(pay_id), "amount": "5000.00"},
        )
        try:
            commit(s, user, id=handle.id, edited_fields={"payment_id": str(uuid4())})
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
        else:
            raise AssertionError("expected INVALID_INPUT for locked-field edit")


def test_update_payment_amount_cross_tenant_rejected():
    with _rollback_session() as s:
        _user_a, _, pay_a = _seed_invoice_with_payment(s, label="A")
        user_b, _, _pay_b = _seed_invoice_with_payment(s, label="B")
        try:
            prepare(
                s, user_b, capability_name="update_payment_amount",
                raw_inputs={"payment_id": str(pay_a), "amount": "5000.00"},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "not found" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT cross-tenant")


def test_update_payment_amount_over_capacity_surfaces_execute_failed():
    """Service caps the new amount at the invoice's remaining capacity. An
    overshoot surfaces as EXECUTE_FAILED at commit. Capacity is computed
    excluding the to-be-voided old payment, so capacity for a 10000-total
    invoice with the old 3000 set aside = 10000."""
    with _rollback_session() as s:
        user, _inv_id, pay_id = _seed_invoice_with_payment(s)
        handle = prepare(
            s, user, capability_name="update_payment_amount",
            raw_inputs={"payment_id": str(pay_id), "amount": "99999.00"},
        )
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.EXECUTE_FAILED
        else:
            raise AssertionError("expected EXECUTE_FAILED for over-capacity amount")


def test_update_payment_amount_on_voided_payment_rejected_at_prepare():
    with _rollback_session() as s:
        user, _inv_id, pay_id = _seed_invoice_with_payment(s)
        from datetime import datetime, timezone
        pay = s.get(Payment, pay_id)
        pay.voided_at = datetime.now(timezone.utc)
        pay.voided_reason = "test"
        pay.voided_by = user.id
        s.add(pay); s.commit()
        try:
            prepare(
                s, user, capability_name="update_payment_amount",
                raw_inputs={"payment_id": str(pay_id), "amount": "1000.00"},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "voided" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for voided payment")


# ---------------------------------------------------------------------------
# Runner
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
