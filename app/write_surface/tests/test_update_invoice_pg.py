"""Postgres-backed tests for the update_invoice capability.

Same shape as the other write-surface PG tests: savepoint-rollback, real
Postgres, prepare -> commit.

Coverage per spec:
    * prepare returns id + preview showing the status / date transition
      explicitly (old -> new for each changed field)
    * commit applies only provided fields (selective patch)
    * a field NOT provided is left untouched
    * status-change preview shows old -> new with STATUS TRANSITION marker
    * an illegal transition (one the service rejects) surfaces as
      EXECUTE_FAILED at commit, not a crash
    * second commit rejected (single-use)
    * editable-field edit at confirm flows through
    * locked-field edit rejected
    * cross-tenant prepare rejected
    * empty-patch rejected at prepare (defensive UX)

Run:
    python -m app.write_surface.tests.test_update_invoice_pg
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlmodel import Session

from app.core.database import engine
from app.core.phone import normalize_phone_value
from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import InvoiceStatus, LeadSource, UserRole
from app.models.invoice import Invoice
from app.models.lead import Lead
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


# --- Fixture ----------------------------------------------------------------

def _d(v) -> Decimal:
    return Decimal(str(v))


def _seed_invoice(
    session: Session, *,
    label: str = "A",
    invoice_status: InvoiceStatus = InvoiceStatus.DRAFT,
    total: Decimal = Decimal("10000.00"),
    issued: date = date(2026, 5, 1),
    due: date = date(2026, 6, 1),
) -> tuple[User, UUID]:
    bid = uuid4()
    session.add(Business(id=bid, name=f"UpdInv Test {label}",
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
        issued_date=issued, due_date=due,
        subtotal=total, tax_total=_d(0), total_amount=total,
        invoice_number=f"INV-{label}",
    ))
    session.commit()
    return user, inv_id


# ===========================================================================
# Preview shape
# ===========================================================================

def test_update_invoice_prepare_status_change_shows_old_to_new():
    """The headline preview test — a status change must show old → new with
    the STATUS TRANSITION marker so the user sees exactly what they approve."""
    with _rollback_session() as s:
        user, inv_id = _seed_invoice(s, invoice_status=InvoiceStatus.DRAFT)
        handle = prepare(
            s, user, capability_name="update_invoice",
            raw_inputs={"invoice_id": str(inv_id), "status": "sent"},
        )
        assert "INV-A" in handle.preview
        assert "Cust A" in handle.preview
        assert "STATUS TRANSITION" in handle.preview
        assert "status: draft → sent" in handle.preview
        assert sorted(handle.editable_fields) == ["due_date", "issued_date", "status"]


def test_update_invoice_prepare_date_change_shows_old_to_new():
    with _rollback_session() as s:
        user, inv_id = _seed_invoice(s)
        handle = prepare(
            s, user, capability_name="update_invoice",
            raw_inputs={"invoice_id": str(inv_id), "due_date": "2026-07-15"},
        )
        # Date-only change — no STATUS TRANSITION marker, but date old → new shown.
        assert "STATUS TRANSITION" not in handle.preview
        assert "due_date: 2026-06-01 → 2026-07-15" in handle.preview


def test_update_invoice_prepare_combined_status_and_date_changes():
    with _rollback_session() as s:
        user, inv_id = _seed_invoice(s, invoice_status=InvoiceStatus.DRAFT)
        handle = prepare(
            s, user, capability_name="update_invoice",
            raw_inputs={
                "invoice_id": str(inv_id),
                "status": "approved",
                "due_date": "2026-07-15",
            },
        )
        assert "STATUS TRANSITION" in handle.preview
        assert "2 change(s)" in handle.preview
        assert "status: draft → approved" in handle.preview
        assert "due_date: 2026-06-01 → 2026-07-15" in handle.preview


# ===========================================================================
# Commit semantics
# ===========================================================================

def test_update_invoice_commit_applies_only_provided_fields():
    """Selective patch: only status is updated; issued_date / due_date stay."""
    with _rollback_session() as s:
        user, inv_id = _seed_invoice(s, invoice_status=InvoiceStatus.DRAFT)
        handle = prepare(
            s, user, capability_name="update_invoice",
            raw_inputs={"invoice_id": str(inv_id), "status": "sent"},
        )
        result = commit(s, user, id=handle.id)
        assert result["updated_fields"] == ["status"]
        assert result["status"] == "sent"

        s.expire_all()
        row = s.get(Invoice, inv_id)
        # Patched:
        assert row.status == InvoiceStatus.SENT
        # Untouched:
        assert row.issued_date == date(2026, 5, 1)
        assert row.due_date == date(2026, 6, 1)
        # Action consumed.
        assert s.get(PreparedAction, handle.id).status == STATUS_CONSUMED


def test_update_invoice_date_only_patch_leaves_status_untouched():
    """The 'field not provided is untouched' guard, specifically for status."""
    with _rollback_session() as s:
        user, inv_id = _seed_invoice(s, invoice_status=InvoiceStatus.DRAFT)
        handle = prepare(
            s, user, capability_name="update_invoice",
            raw_inputs={"invoice_id": str(inv_id), "due_date": "2026-07-15"},
        )
        commit(s, user, id=handle.id)
        s.expire_all()
        row = s.get(Invoice, inv_id)
        assert row.due_date == date(2026, 7, 15)
        # Status was NOT in the patch — must remain DRAFT.
        assert row.status == InvoiceStatus.DRAFT


def test_update_invoice_status_to_approved_logs_lead_activity():
    """Smoke-check that the service's APPROVED side-effect (lead activity
    row) still runs through the capability path."""
    from app.models.lead import LeadActivity
    from app.models.enums import LeadActivityType
    from sqlmodel import select

    with _rollback_session() as s:
        user, inv_id = _seed_invoice(s, invoice_status=InvoiceStatus.SENT)
        handle = prepare(
            s, user, capability_name="update_invoice",
            raw_inputs={"invoice_id": str(inv_id), "status": "approved"},
        )
        commit(s, user, id=handle.id)
        s.expire_all()
        invoice = s.get(Invoice, inv_id)
        assert invoice.status == InvoiceStatus.APPROVED
        activities = s.exec(
            select(LeadActivity).where(LeadActivity.lead_id == invoice.lead_id)
        ).all()
        assert any(a.type == LeadActivityType.INVOICE_APPROVED for a in activities)


def test_update_invoice_no_change_patch_on_date_is_idempotent_no_op():
    """Patch with a DATE value matching current = preview says 'no changes'
    and commit is a no-op. Note: this only holds for date fields. A status
    same-value patch (status=draft on a draft) is REJECTED by the service's
    state machine — same-value isn't in `allowed_transitions` — so it
    surfaces as EXECUTE_FAILED, not a no-op. Tested separately below."""
    with _rollback_session() as s:
        user, inv_id = _seed_invoice(s, invoice_status=InvoiceStatus.DRAFT)
        # due_date matches the seeded value (2026-06-01).
        handle = prepare(
            s, user, capability_name="update_invoice",
            raw_inputs={"invoice_id": str(inv_id), "due_date": "2026-06-01"},
        )
        assert "no changes" in handle.preview
        commit(s, user, id=handle.id)   # must not raise
        assert s.get(Invoice, inv_id).due_date == date(2026, 6, 1)


def test_update_invoice_status_same_value_rejected_by_service_state_machine():
    """status=draft on an already-DRAFT invoice is NOT a legal transition
    (the service's `allowed_transitions[DRAFT]` is {SENT, APPROVED} — same
    -value isn't there). Confirms the capability defers all state-machine
    enforcement to the service, including this corner."""
    with _rollback_session() as s:
        user, inv_id = _seed_invoice(s, invoice_status=InvoiceStatus.DRAFT)
        handle = prepare(
            s, user, capability_name="update_invoice",
            raw_inputs={"invoice_id": str(inv_id), "status": "draft"},
        )
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.EXECUTE_FAILED
        else:
            raise AssertionError(
                "expected EXECUTE_FAILED: status=draft on DRAFT is rejected by the service")


# ===========================================================================
# Single-use + edit semantics
# ===========================================================================

def test_update_invoice_second_commit_rejected():
    with _rollback_session() as s:
        user, inv_id = _seed_invoice(s, invoice_status=InvoiceStatus.DRAFT)
        handle = prepare(
            s, user, capability_name="update_invoice",
            raw_inputs={"invoice_id": str(inv_id), "status": "sent"},
        )
        commit(s, user, id=handle.id)
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_update_invoice_edit_editable_field_flows_through():
    with _rollback_session() as s:
        user, inv_id = _seed_invoice(s, invoice_status=InvoiceStatus.DRAFT)
        handle = prepare(
            s, user, capability_name="update_invoice",
            raw_inputs={"invoice_id": str(inv_id), "due_date": "2026-07-15"},
        )
        commit(s, user, id=handle.id, edited_fields={"due_date": "2026-08-01"})
        s.expire_all()
        assert s.get(Invoice, inv_id).due_date == date(2026, 8, 1)


def test_update_invoice_edit_locked_field_rejected():
    with _rollback_session() as s:
        user, inv_id = _seed_invoice(s, invoice_status=InvoiceStatus.DRAFT)
        handle = prepare(
            s, user, capability_name="update_invoice",
            raw_inputs={"invoice_id": str(inv_id), "status": "sent"},
        )
        try:
            commit(s, user, id=handle.id, edited_fields={"invoice_id": str(uuid4())})
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "invoice_id" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for locked-field edit")


# ===========================================================================
# Cross-tenant + defensive UX
# ===========================================================================

def test_update_invoice_cross_tenant_rejected():
    with _rollback_session() as s:
        _user_a, inv_a = _seed_invoice(s, label="A", invoice_status=InvoiceStatus.DRAFT)
        user_b, _inv_b = _seed_invoice(s, label="B", invoice_status=InvoiceStatus.DRAFT)
        try:
            prepare(
                s, user_b, capability_name="update_invoice",
                raw_inputs={"invoice_id": str(inv_a), "status": "sent"},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "not found" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT cross-tenant")


def test_update_invoice_no_editable_fields_rejected_at_prepare():
    with _rollback_session() as s:
        user, inv_id = _seed_invoice(s, invoice_status=InvoiceStatus.DRAFT)
        try:
            prepare(
                s, user, capability_name="update_invoice",
                raw_inputs={"invoice_id": str(inv_id)},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "no fields to update" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for empty patch")


# ===========================================================================
# Service-enforced state machine (illegal transitions)
# ===========================================================================

def test_update_invoice_illegal_transition_surfaces_execute_failed():
    """APPROVED → SENT is NOT a legal transition through this service.
    The service refuses; the engine surfaces it cleanly as EXECUTE_FAILED
    with the service's exact reason. NOT a 500, NOT a crash."""
    with _rollback_session() as s:
        user, inv_id = _seed_invoice(s, invoice_status=InvoiceStatus.APPROVED)
        handle = prepare(
            s, user, capability_name="update_invoice",
            raw_inputs={"invoice_id": str(inv_id), "status": "sent"},
        )
        # Preview happily showed approved → sent (validate doesn't gate the
        # state machine — the service does). Commit surfaces the refusal.
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.EXECUTE_FAILED
        else:
            raise AssertionError("expected EXECUTE_FAILED on illegal transition")
        # Action consumed, but invoice status untouched.
        s.expire_all()
        assert s.get(PreparedAction, handle.id).status == STATUS_CONSUMED
        assert s.get(Invoice, inv_id).status == InvoiceStatus.APPROVED


def test_update_invoice_on_paid_invoice_surfaces_execute_failed():
    """PAID invoices refuse all edits at the service. Same shape — clean
    EXECUTE_FAILED."""
    with _rollback_session() as s:
        user, inv_id = _seed_invoice(s, invoice_status=InvoiceStatus.PAID)
        handle = prepare(
            s, user, capability_name="update_invoice",
            raw_inputs={"invoice_id": str(inv_id), "due_date": "2026-07-15"},
        )
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.EXECUTE_FAILED
        else:
            raise AssertionError("expected EXECUTE_FAILED on paid invoice")


def test_update_invoice_on_cancelled_invoice_surfaces_execute_failed():
    with _rollback_session() as s:
        user, inv_id = _seed_invoice(s, invoice_status=InvoiceStatus.CANCELLED)
        handle = prepare(
            s, user, capability_name="update_invoice",
            raw_inputs={"invoice_id": str(inv_id), "due_date": "2026-07-15"},
        )
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.EXECUTE_FAILED
        else:
            raise AssertionError("expected EXECUTE_FAILED on cancelled invoice")


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
