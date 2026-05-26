"""Postgres-backed tests for cancel_invoice + add_invoice_adjustment.

Same shape as the other write-surface PG tests: savepoint-rollback, real
Postgres, prepare -> commit per capability. Per cap:

    * prepare returns id + preview (with invoice-identifying info)
    * commit performs the operation on the row
    * second commit rejected (single-use)
    * editable-field edit at confirm flows through
    * locked-field edit rejected
    * cross-tenant prepare rejected (read returns zero rows ViaParent-scoped
      for the invoice; capability's validate raises ValueError ->
      engine returns INVALID_INPUT)

cancel_invoice additionally:
    * service-side refusal (e.g. invoice has payments) surfaces as
      EXECUTE_FAILED at commit, not a crash, and leaves the action consumed.

Run:
    python -m app.write_surface.tests.test_invoice_caps_pg
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlmodel import Session

from app.core.database import engine
from app.core.phone import normalize_phone_value
from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import InvoiceStatus, LeadSource, PaymentMethod, UserRole
from app.models.invoice import Invoice
from app.models.invoice_adjustment import InvoiceAdjustment
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


# --- Fixture: business + user + lead + invoice (status configurable) --------

def _d(v) -> Decimal:
    return Decimal(str(v))


def _seed_invoice(
    session: Session, *,
    label: str = "A",
    invoice_status: InvoiceStatus = InvoiceStatus.APPROVED,
    total: Decimal = Decimal("10000.00"),
) -> tuple[User, UUID, UUID]:
    """Seed a business + owner + customer + lead + invoice. Returns
    (user, invoice_id, lead_id)."""
    bid = uuid4()
    session.add(Business(id=bid, name=f"Inv Test {label}",
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
        status=invoice_status,
        issued_date=date(2026, 5, 1),
        due_date=date(2026, 6, 1),
        subtotal=total, tax_total=_d(0), total_amount=total,
        invoice_number=f"INV-{label}",
    ))
    session.commit()
    return user, inv_id, lead_id


# ===========================================================================
# cancel_invoice
# ===========================================================================

def test_cancel_invoice_prepare_returns_preview():
    with _rollback_session() as s:
        user, inv_id, _ = _seed_invoice(s)
        handle = prepare(
            s, user, capability_name="cancel_invoice",
            raw_inputs={"invoice_id": str(inv_id), "reason": "customer dropped"},
        )
        assert isinstance(handle.id, str) and len(handle.id) > 30
        # Preview names the invoice + owner + status + total + reason.
        assert "INV-A" in handle.preview
        assert "Cust A" in handle.preview
        assert "approved" in handle.preview
        assert "10000" in handle.preview
        assert "customer dropped" in handle.preview
        assert list(handle.editable_fields) == ["reason"]


def test_cancel_invoice_commit_flips_status():
    with _rollback_session() as s:
        user, inv_id, _ = _seed_invoice(s)
        handle = prepare(
            s, user, capability_name="cancel_invoice",
            raw_inputs={"invoice_id": str(inv_id)},
        )
        result = commit(s, user, id=handle.id)
        assert result["status"] == "cancelled"
        assert result["cancelled_at"] is not None
        s.expire_all()
        row = s.get(Invoice, inv_id)
        assert row.status == InvoiceStatus.CANCELLED
        assert row.cancelled_at is not None
        # Action consumed
        assert s.get(PreparedAction, handle.id).status == STATUS_CONSUMED


def test_cancel_invoice_second_commit_rejected():
    with _rollback_session() as s:
        user, inv_id, _ = _seed_invoice(s)
        handle = prepare(
            s, user, capability_name="cancel_invoice",
            raw_inputs={"invoice_id": str(inv_id)},
        )
        commit(s, user, id=handle.id)
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_cancel_invoice_edit_reason_flows_through():
    with _rollback_session() as s:
        user, inv_id, _ = _seed_invoice(s)
        handle = prepare(
            s, user, capability_name="cancel_invoice",
            raw_inputs={"invoice_id": str(inv_id), "reason": "agent reason"},
        )
        commit(s, user, id=handle.id, edited_fields={"reason": "human edited reason"})
        s.expire_all()
        row = s.get(Invoice, inv_id)
        assert row.cancelled_reason == "human edited reason"


def test_cancel_invoice_edit_locked_field_rejected():
    with _rollback_session() as s:
        user, inv_id, _ = _seed_invoice(s)
        handle = prepare(
            s, user, capability_name="cancel_invoice",
            raw_inputs={"invoice_id": str(inv_id)},
        )
        try:
            commit(s, user, id=handle.id, edited_fields={"invoice_id": str(uuid4())})
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "invoice_id" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for locked-field edit")


def test_cancel_invoice_cross_tenant_rejected():
    with _rollback_session() as s:
        _user_a, inv_a, _ = _seed_invoice(s, label="A")
        user_b, _inv_b, _ = _seed_invoice(s, label="B")
        try:
            prepare(
                s, user_b, capability_name="cancel_invoice",
                raw_inputs={"invoice_id": str(inv_a)},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "not found" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT cross-tenant")


def test_cancel_invoice_already_paid_surfaces_execute_failed_not_crash():
    """An invoice that has payments must be refused by the SERVICE at commit
    time. The engine surfaces it as EXECUTE_FAILED cleanly; the action stays
    consumed. NOT a crash, NOT a 500. Mirrors the same shape as the
    record_payment-against-cancelled-invoice test."""
    with _rollback_session() as s:
        user, inv_id, _ = _seed_invoice(s)
        handle = prepare(
            s, user, capability_name="cancel_invoice",
            raw_inputs={"invoice_id": str(inv_id)},
        )
        # Between prepare and commit, a payment is recorded. The service
        # refuses cancellation (defence-in-depth: payments exist).
        s.add(Payment(
            id=uuid4(), business_id=user.business_id, invoice_id=inv_id,
            amount=_d(1000), payment_method=PaymentMethod.CASH,
            payment_date=date(2026, 5, 10),
        ))
        s.commit()

        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.EXECUTE_FAILED
        else:
            raise AssertionError("expected EXECUTE_FAILED when payments exist")

        # And the action is consumed (atomic claim ran before execute).
        s.expire_all()
        assert s.get(PreparedAction, handle.id).status == STATUS_CONSUMED
        # The invoice was NOT cancelled.
        assert s.get(Invoice, inv_id).status == InvoiceStatus.APPROVED


# ===========================================================================
# add_invoice_adjustment
# ===========================================================================

def test_add_invoice_adjustment_prepare_shows_balance_impact():
    """Preview must surface where the balance lands after the adjustment."""
    with _rollback_session() as s:
        user, inv_id, _ = _seed_invoice(s)   # total 10000, no payments yet
        handle = prepare(
            s, user, capability_name="add_invoice_adjustment",
            raw_inputs={
                "invoice_id": str(inv_id),
                "adjustment_type": "discount",
                "amount": "2500.00",
                "reason": "loyalty",
            },
        )
        # Preview should mention: kind, amount, invoice, current balance, new balance.
        assert "DISCOUNT" in handle.preview
        assert "2,500.00" in handle.preview
        assert "INV-A" in handle.preview
        assert "current balance" in handle.preview and "new balance" in handle.preview
        assert "10,000.00" in handle.preview      # current
        assert "7,500.00" in handle.preview       # new
        assert "loyalty" in handle.preview
        assert sorted(handle.editable_fields) == ["amount", "reason"]


def test_add_invoice_adjustment_commit_creates_row_and_recomputes():
    with _rollback_session() as s:
        user, inv_id, _ = _seed_invoice(s)   # APPROVED, total 10000
        handle = prepare(
            s, user, capability_name="add_invoice_adjustment",
            raw_inputs={
                "invoice_id": str(inv_id),
                "adjustment_type": "write_off",
                "amount": "10000.00",
            },
        )
        result = commit(s, user, id=handle.id)
        assert "adjustment_id" in result
        assert result["adjustment_type"] == "write_off"

        s.expire_all()
        adj = s.get(InvoiceAdjustment, UUID(result["adjustment_id"]))
        assert adj is not None
        assert adj.invoice_id == inv_id
        assert adj.amount == _d("10000.00")
        assert adj.adjustment_type == "write_off"
        # Full write-off of the entire balance → status recomputed to PAID.
        assert s.get(Invoice, inv_id).status == InvoiceStatus.PAID


def test_add_invoice_adjustment_second_commit_rejected():
    with _rollback_session() as s:
        user, inv_id, _ = _seed_invoice(s)
        handle = prepare(
            s, user, capability_name="add_invoice_adjustment",
            raw_inputs={
                "invoice_id": str(inv_id),
                "adjustment_type": "discount",
                "amount": "500.00",
            },
        )
        commit(s, user, id=handle.id)
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_add_invoice_adjustment_edit_amount_flows_through():
    with _rollback_session() as s:
        user, inv_id, _ = _seed_invoice(s)
        handle = prepare(
            s, user, capability_name="add_invoice_adjustment",
            raw_inputs={
                "invoice_id": str(inv_id),
                "adjustment_type": "discount",
                "amount": "500.00",
            },
        )
        result = commit(s, user, id=handle.id, edited_fields={
            "amount": "750.00",
            "reason": "human bumped it",
        })
        adj = s.get(InvoiceAdjustment, UUID(result["adjustment_id"]))
        assert adj.amount == _d("750.00")
        assert adj.reason == "human bumped it"


def test_add_invoice_adjustment_edit_locked_field_rejected():
    with _rollback_session() as s:
        user, inv_id, _ = _seed_invoice(s)
        handle = prepare(
            s, user, capability_name="add_invoice_adjustment",
            raw_inputs={
                "invoice_id": str(inv_id),
                "adjustment_type": "discount",
                "amount": "500.00",
            },
        )
        # Attempt to switch adjustment_type at commit time → must be rejected.
        try:
            commit(s, user, id=handle.id, edited_fields={"adjustment_type": "write_off"})
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "adjustment_type" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for locked-field edit")


def test_add_invoice_adjustment_cross_tenant_rejected():
    with _rollback_session() as s:
        _user_a, inv_a, _ = _seed_invoice(s, label="A")
        user_b, _inv_b, _ = _seed_invoice(s, label="B")
        try:
            prepare(
                s, user_b, capability_name="add_invoice_adjustment",
                raw_inputs={
                    "invoice_id": str(inv_a),
                    "adjustment_type": "discount",
                    "amount": "500.00",
                },
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "not found" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT cross-tenant")


def test_add_invoice_adjustment_amount_exceeds_balance_surfaces_execute_failed():
    """Service enforces amount <= remaining balance. We rely on it; an
    overshoot surfaces as EXECUTE_FAILED at commit. The validate hook does
    NOT pre-block it — that would duplicate business logic in two places."""
    with _rollback_session() as s:
        user, inv_id, _ = _seed_invoice(s)   # balance 10000
        handle = prepare(
            s, user, capability_name="add_invoice_adjustment",
            raw_inputs={
                "invoice_id": str(inv_id),
                "adjustment_type": "discount",
                "amount": "99999.00",        # way over the balance
            },
        )
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.EXECUTE_FAILED
        else:
            raise AssertionError("expected EXECUTE_FAILED for over-balance amount")
        # Action is consumed; no adjustment row was created.
        s.expire_all()
        assert s.get(PreparedAction, handle.id).status == STATUS_CONSUMED


def test_add_invoice_adjustment_on_draft_surfaces_execute_failed():
    """Draft invoices are refused by the service. Same pattern — surfaces as
    EXECUTE_FAILED on commit. Preview-time validate does not pre-block (the
    service is the single source of truth for status rules)."""
    with _rollback_session() as s:
        user, inv_id, _ = _seed_invoice(s, invoice_status=InvoiceStatus.DRAFT)
        handle = prepare(
            s, user, capability_name="add_invoice_adjustment",
            raw_inputs={
                "invoice_id": str(inv_id),
                "adjustment_type": "discount",
                "amount": "500.00",
            },
        )
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.EXECUTE_FAILED
        else:
            raise AssertionError("expected EXECUTE_FAILED on draft invoice")


def test_add_invoice_adjustment_on_cancelled_invoice_rejected_at_prepare():
    """One of the few service rules that's ALSO mirrored at validate time:
    a cancelled invoice has is_cancelled=True in the read model, the validate
    hook flags it immediately as INVALID_INPUT so the LLM sees the reason
    without burning a confirm."""
    with _rollback_session() as s:
        user, inv_id, _ = _seed_invoice(s, invoice_status=InvoiceStatus.CANCELLED)
        try:
            prepare(
                s, user, capability_name="add_invoice_adjustment",
                raw_inputs={
                    "invoice_id": str(inv_id),
                    "adjustment_type": "discount",
                    "amount": "500.00",
                },
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "cancelled" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for cancelled invoice")


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
