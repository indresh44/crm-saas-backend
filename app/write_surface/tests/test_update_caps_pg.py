"""Postgres-backed tests for the update_lead and update_customer capabilities.

Same shape as the other write-surface PG tests: savepoint-rollback,
prepare -> commit. Per cap, the spec battery is:

    * prepare returns id + preview showing the changed fields (old -> new)
    * commit applies only the provided fields
    * a field NOT provided is left untouched (selective patch)
    * second commit on the same handle is rejected (single-use)
    * editing an editable field at confirm flows through
    * editing a locked field is rejected
    * cross-tenant: an entity belonging to another business cannot be
      prepared against (read returns zero rows, validate raises ValueError,
      engine returns INVALID_INPUT)

Run:
    python -m app.write_surface.tests.test_update_caps_pg
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
from app.models.enums import LeadSource, UserRole
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


# --- Fixture helpers --------------------------------------------------------

def _seed_business_user_lead_customer(
    session: Session, *, label: str = "A",
) -> tuple[User, UUID, UUID]:
    """Insert business + owner + pipeline + customer + lead. Returns
    (user, lead_id, customer_id). Customer has a baseline name + phone."""
    bid = uuid4()
    session.add(Business(id=bid, name=f"Upd Test {label}",
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
    base_phone = f"+91 9999900{label}"
    session.add(Customer(
        id=cust_id, business_id=bid,
        name=f"Cust {label}",
        phone=base_phone,
        phone_normalized=normalize_phone_value(base_phone),
        email=f"cust-{label}@test.local",
        city="Pune", state="MH",
    ))
    session.flush()
    lead_id = uuid4()
    session.add(Lead(
        id=lead_id, business_id=bid, customer_id=cust_id, stage_id=sid,
        title=f"Lead {label}",
        source=LeadSource.WHATSAPP,
        notes="initial notes",
        estimated_value=Decimal("10000.00"),
    ))
    session.commit()
    return user, lead_id, cust_id


# ===========================================================================
# update_lead
# ===========================================================================

def test_update_lead_prepare_shows_changed_fields_old_to_new():
    with _rollback_session() as s:
        user, lead_id, _ = _seed_business_user_lead_customer(s)
        handle = prepare(
            s, user, capability_name="update_lead",
            raw_inputs={
                "lead_id": str(lead_id),
                "title": "Lead A (renamed)",
                "notes": "updated notes",
            },
        )
        assert isinstance(handle.id, str) and len(handle.id) > 30
        # Preview lists the lead by title and shows BOTH changed fields.
        assert "Lead A" in handle.preview
        assert "2 change(s)" in handle.preview
        assert "title: Lead A → Lead A (renamed)" in handle.preview
        assert "notes: initial notes → updated notes" in handle.preview
        # All editable fields are listed as editable_fields on the handle.
        assert set(handle.editable_fields) == {
            "title", "notes", "estimated_value", "source",
            "service_date", "follow_up_at", "assigned_to",
        }


def test_update_lead_commit_applies_only_provided_fields():
    """Selective patch: only `notes` is updated; title, estimated_value,
    source remain at their seeded values."""
    with _rollback_session() as s:
        user, lead_id, _ = _seed_business_user_lead_customer(s)
        handle = prepare(
            s, user, capability_name="update_lead",
            raw_inputs={"lead_id": str(lead_id), "notes": "patched only"},
        )
        result = commit(s, user, id=handle.id)
        assert result["updated_fields"] == ["notes"]
        s.expire_all()
        row = s.get(Lead, lead_id)
        # The patched field changed:
        assert row.notes == "patched only"
        # The unmentioned fields are STILL the seeded values:
        assert row.title == "Lead A"
        assert row.estimated_value == Decimal("10000.00")
        assert row.source == LeadSource.WHATSAPP
        # Action consumed
        assert s.get(PreparedAction, handle.id).status == STATUS_CONSUMED


def test_update_lead_decimal_and_date_fields_persist():
    """Cover the typed inputs (decimal, date, datetime) end-to-end."""
    with _rollback_session() as s:
        user, lead_id, _ = _seed_business_user_lead_customer(s)
        when = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc).isoformat()
        handle = prepare(
            s, user, capability_name="update_lead",
            raw_inputs={
                "lead_id": str(lead_id),
                "estimated_value": "55000.50",
                "service_date": "2026-07-15",
                "follow_up_at": when,
            },
        )
        result = commit(s, user, id=handle.id)
        assert set(result["updated_fields"]) == {
            "estimated_value", "service_date", "follow_up_at",
        }
        s.expire_all()
        row = s.get(Lead, lead_id)
        assert row.estimated_value == Decimal("55000.50")
        assert row.service_date == date(2026, 7, 15)
        assert row.follow_up_at.date() == date(2026, 6, 1)


def test_update_lead_enum_source_persists():
    with _rollback_session() as s:
        user, lead_id, _ = _seed_business_user_lead_customer(s)
        handle = prepare(
            s, user, capability_name="update_lead",
            raw_inputs={"lead_id": str(lead_id), "source": "referral"},
        )
        commit(s, user, id=handle.id)
        s.expire_all()
        assert s.get(Lead, lead_id).source == LeadSource.REFERRAL


def test_update_lead_second_commit_rejected():
    with _rollback_session() as s:
        user, lead_id, _ = _seed_business_user_lead_customer(s)
        handle = prepare(
            s, user, capability_name="update_lead",
            raw_inputs={"lead_id": str(lead_id), "title": "v2"},
        )
        commit(s, user, id=handle.id)
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_update_lead_edit_editable_field_flows_through():
    with _rollback_session() as s:
        user, lead_id, _ = _seed_business_user_lead_customer(s)
        handle = prepare(
            s, user, capability_name="update_lead",
            raw_inputs={"lead_id": str(lead_id), "title": "agent proposed"},
        )
        commit(s, user, id=handle.id, edited_fields={"title": "human edited"})
        s.expire_all()
        assert s.get(Lead, lead_id).title == "human edited"


def test_update_lead_edit_locked_field_rejected():
    with _rollback_session() as s:
        user, lead_id, _ = _seed_business_user_lead_customer(s)
        handle = prepare(
            s, user, capability_name="update_lead",
            raw_inputs={"lead_id": str(lead_id), "title": "x"},
        )
        try:
            commit(s, user, id=handle.id, edited_fields={"lead_id": str(uuid4())})
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "lead_id" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for locked-field edit")


def test_update_lead_cross_tenant_rejected():
    with _rollback_session() as s:
        _user_a, lead_a, _ = _seed_business_user_lead_customer(s, label="A")
        user_b, _lead_b, _ = _seed_business_user_lead_customer(s, label="B")
        try:
            prepare(
                s, user_b, capability_name="update_lead",
                raw_inputs={"lead_id": str(lead_a), "title": "hijack"},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "not found" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT cross-tenant")


def test_update_lead_no_editable_fields_rejected_at_prepare():
    """User supplies only the LOCKED id and nothing else — there's literally
    nothing to update. Caught at validate (not committed as a no-op)."""
    with _rollback_session() as s:
        user, lead_id, _ = _seed_business_user_lead_customer(s)
        try:
            prepare(
                s, user, capability_name="update_lead",
                raw_inputs={"lead_id": str(lead_id)},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "no fields to update" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for empty patch")


# ===========================================================================
# update_customer
# ===========================================================================

def test_update_customer_prepare_shows_changed_fields():
    with _rollback_session() as s:
        user, _, cust_id = _seed_business_user_lead_customer(s)
        handle = prepare(
            s, user, capability_name="update_customer",
            raw_inputs={
                "customer_id": str(cust_id),
                "name": "Cust A (renamed)",
                "city": "Mumbai",
            },
        )
        assert "Cust A" in handle.preview
        assert "2 change(s)" in handle.preview
        assert "name: Cust A → Cust A (renamed)" in handle.preview
        assert "city: Pune → Mumbai" in handle.preview
        assert set(handle.editable_fields) == {
            "name", "phone", "email", "address",
            "city", "state", "gst_number", "notes",
        }


def test_update_customer_commit_applies_only_provided_fields():
    with _rollback_session() as s:
        user, _, cust_id = _seed_business_user_lead_customer(s)
        handle = prepare(
            s, user, capability_name="update_customer",
            raw_inputs={"customer_id": str(cust_id), "city": "Mumbai"},
        )
        commit(s, user, id=handle.id)
        s.expire_all()
        row = s.get(Customer, cust_id)
        assert row.city == "Mumbai"
        # Unmentioned fields untouched
        assert row.name == "Cust A"
        assert row.state == "MH"
        assert row.email == "cust-A@test.local"


def test_update_customer_phone_change_normalises_via_service():
    """The service normalises phone + duplicate-checks. We rely on it; we
    don't re-normalise here. Verify phone_normalized was set by the service
    when the capability supplied a display-format phone."""
    with _rollback_session() as s:
        user, _, cust_id = _seed_business_user_lead_customer(s)
        handle = prepare(
            s, user, capability_name="update_customer",
            raw_inputs={"customer_id": str(cust_id), "phone": "+91 88888 77777"},
        )
        commit(s, user, id=handle.id)
        s.expire_all()
        row = s.get(Customer, cust_id)
        assert row.phone == "+91 88888 77777"
        assert row.phone_normalized == normalize_phone_value("+91 88888 77777")


def test_update_customer_phone_duplicate_rejected_by_service():
    """Two customers in the same business; trying to update #2's phone to
    match #1's must be rejected by the service (409 -> EXECUTE_FAILED)."""
    with _rollback_session() as s:
        user, _, cust_one = _seed_business_user_lead_customer(s, label="A")
        # second customer in the SAME business
        cust_two = uuid4()
        s.add(Customer(
            id=cust_two, business_id=user.business_id,
            name="Other Cust",
            phone="+91 7777700000",
            phone_normalized=normalize_phone_value("+91 7777700000"),
        ))
        s.commit()

        cust_one_phone = s.get(Customer, cust_one).phone
        handle = prepare(
            s, user, capability_name="update_customer",
            raw_inputs={"customer_id": str(cust_two), "phone": cust_one_phone},
        )
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            # Service raises HTTPException(409); engine surfaces as EXECUTE_FAILED.
            assert exc.code == ErrorCode.EXECUTE_FAILED
        else:
            raise AssertionError("expected EXECUTE_FAILED on phone duplicate")


def test_update_customer_second_commit_rejected():
    with _rollback_session() as s:
        user, _, cust_id = _seed_business_user_lead_customer(s)
        handle = prepare(
            s, user, capability_name="update_customer",
            raw_inputs={"customer_id": str(cust_id), "name": "Renamed"},
        )
        commit(s, user, id=handle.id)
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_update_customer_edit_editable_field_flows_through():
    with _rollback_session() as s:
        user, _, cust_id = _seed_business_user_lead_customer(s)
        handle = prepare(
            s, user, capability_name="update_customer",
            raw_inputs={"customer_id": str(cust_id), "name": "agent proposed"},
        )
        commit(s, user, id=handle.id, edited_fields={"name": "human edited"})
        s.expire_all()
        assert s.get(Customer, cust_id).name == "human edited"


def test_update_customer_edit_locked_field_rejected():
    with _rollback_session() as s:
        user, _, cust_id = _seed_business_user_lead_customer(s)
        handle = prepare(
            s, user, capability_name="update_customer",
            raw_inputs={"customer_id": str(cust_id), "name": "x"},
        )
        try:
            commit(s, user, id=handle.id, edited_fields={"customer_id": str(uuid4())})
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "customer_id" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for locked-field edit")


def test_update_customer_cross_tenant_rejected():
    with _rollback_session() as s:
        _user_a, _, cust_a = _seed_business_user_lead_customer(s, label="A")
        user_b, _, _cust_b = _seed_business_user_lead_customer(s, label="B")
        try:
            prepare(
                s, user_b, capability_name="update_customer",
                raw_inputs={"customer_id": str(cust_a), "name": "hijack"},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "not found" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT cross-tenant")


def test_update_customer_no_editable_fields_rejected_at_prepare():
    with _rollback_session() as s:
        user, _, cust_id = _seed_business_user_lead_customer(s)
        try:
            prepare(
                s, user, capability_name="update_customer",
                raw_inputs={"customer_id": str(cust_id)},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "no fields to update" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for empty patch")


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
