"""Postgres-backed tests for the three catalog capabilities.

Same shape as the other write-surface PG tests: savepoint-rollback, real
Postgres, prepare -> commit. Covers create / update / deactivate per the
Batch 6 spec.

Run:
    python -m app.write_surface.tests.test_catalog_caps_pg
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlmodel import Session

from app.core.database import engine
from app.models.business import Business
from app.models.catalog_item import CatalogItem
from app.models.enums import CatalogItemUnit, UserRole
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


def _seed_user(session: Session, *, label: str = "A") -> User:
    bid = uuid4()
    session.add(Business(id=bid, name=f"Cat Test {label}",
                         phone=f"99900000{label}"))
    session.flush()
    user = User(
        id=uuid4(), business_id=bid,
        name=f"Owner {label}", email=f"owner-{bid}@test.local",
        role=UserRole.OWNER, is_active=True,
    )
    session.add(user); session.commit()
    return user


def _seed_catalog_item(session: Session, *, user: User, name: str = "Modular Kitchen",
                       rate: Decimal = Decimal("100000.00"),
                       gst: Decimal = Decimal("18.00"),
                       unit: CatalogItemUnit = CatalogItemUnit.PIECE,
                       custom_unit=None,
                       is_active: bool = True) -> UUID:
    item_id = uuid4()
    session.add(CatalogItem(
        id=item_id, business_id=user.business_id,
        name=name, default_rate=rate, gst_percent=gst,
        unit=unit, custom_unit=custom_unit,
        is_active=is_active,
    ))
    session.commit()
    return item_id


# ===========================================================================
# create_catalog_item
# ===========================================================================

def test_create_catalog_item_prepare_returns_preview():
    with _rollback_session() as s:
        user = _seed_user(s)
        handle = prepare(
            s, user, capability_name="create_catalog_item",
            raw_inputs={
                "name": "Modular Kitchen",
                "default_rate": "100000.00",
                "gst_percent": "18.00",
                "unit": "piece",
            },
        )
        assert isinstance(handle.id, str) and len(handle.id) > 30
        assert "Modular Kitchen" in handle.preview
        assert "100,000.00" in handle.preview
        assert "18.00% GST" in handle.preview
        # All inputs are editable on a create.
        assert "name" in handle.editable_fields


def test_create_catalog_item_commit_creates_row():
    with _rollback_session() as s:
        user = _seed_user(s)
        handle = prepare(
            s, user, capability_name="create_catalog_item",
            raw_inputs={"name": "Wardrobe", "default_rate": "50000.00"},
        )
        result = commit(s, user, id=handle.id)
        assert "item_id" in result and result["name"] == "Wardrobe"
        assert result["is_active"] is True
        s.expire_all()
        row = s.get(CatalogItem, UUID(result["item_id"]))
        assert row.default_rate == _d("50000.00")
        assert row.business_id == user.business_id
        # gst_percent defaulted by the model to 18.
        assert row.gst_percent == _d("18.00")
        assert s.get(PreparedAction, handle.id).status == STATUS_CONSUMED


def test_create_catalog_item_second_commit_rejected():
    with _rollback_session() as s:
        user = _seed_user(s)
        handle = prepare(
            s, user, capability_name="create_catalog_item",
            raw_inputs={"name": "Once-only", "default_rate": "1.00"},
        )
        commit(s, user, id=handle.id)
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_create_catalog_item_edit_at_confirm_flows_through():
    with _rollback_session() as s:
        user = _seed_user(s)
        handle = prepare(
            s, user, capability_name="create_catalog_item",
            raw_inputs={"name": "agent suggested", "default_rate": "10.00"},
        )
        result = commit(s, user, id=handle.id, edited_fields={
            "name": "human renamed", "default_rate": "12.50",
        })
        row = s.get(CatalogItem, UUID(result["item_id"]))
        assert row.name == "human renamed"
        assert row.default_rate == _d("12.50")


def test_create_catalog_item_duplicate_name_surfaces_execute_failed():
    """Service rejects duplicate names within the business; engine wraps
    the 400 cleanly as EXECUTE_FAILED, action is consumed, no row created."""
    with _rollback_session() as s:
        user = _seed_user(s)
        _seed_catalog_item(s, user=user, name="Existing")
        handle = prepare(
            s, user, capability_name="create_catalog_item",
            raw_inputs={"name": "Existing", "default_rate": "999.00"},
        )
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.EXECUTE_FAILED
        else:
            raise AssertionError("expected EXECUTE_FAILED for duplicate name")
        s.expire_all()
        assert s.get(PreparedAction, handle.id).status == STATUS_CONSUMED


# ===========================================================================
# update_catalog_item
# ===========================================================================

def test_update_catalog_item_prepare_shows_changes():
    with _rollback_session() as s:
        user = _seed_user(s)
        item_id = _seed_catalog_item(s, user=user, name="Modular Kitchen",
                                     rate=_d("100000.00"))
        handle = prepare(
            s, user, capability_name="update_catalog_item",
            raw_inputs={
                "item_id": str(item_id),
                "name": "Modular Kitchen (premium)",
                "default_rate": "125000.00",
            },
        )
        assert "Modular Kitchen" in handle.preview
        assert "2 change(s)" in handle.preview
        assert "name: Modular Kitchen → Modular Kitchen (premium)" in handle.preview
        assert "default_rate: 100000.00 → 125000.00" in handle.preview


def test_update_catalog_item_selective_patch_leaves_unmentioned_fields():
    """The mandatory selective-patch guard: patching only `default_rate`
    leaves name, unit, gst_percent at their seeded values."""
    with _rollback_session() as s:
        user = _seed_user(s)
        item_id = _seed_catalog_item(s, user=user)
        handle = prepare(
            s, user, capability_name="update_catalog_item",
            raw_inputs={"item_id": str(item_id), "default_rate": "150000.00"},
        )
        commit(s, user, id=handle.id)
        s.expire_all()
        row = s.get(CatalogItem, item_id)
        assert row.default_rate == _d("150000.00")
        # Untouched:
        assert row.name == "Modular Kitchen"
        assert row.gst_percent == _d("18.00")
        assert row.unit == CatalogItemUnit.PIECE
        assert row.is_active is True


def test_update_catalog_item_second_commit_rejected():
    with _rollback_session() as s:
        user = _seed_user(s)
        item_id = _seed_catalog_item(s, user=user)
        handle = prepare(
            s, user, capability_name="update_catalog_item",
            raw_inputs={"item_id": str(item_id), "name": "renamed"},
        )
        commit(s, user, id=handle.id)
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_update_catalog_item_edit_at_confirm_flows_through():
    with _rollback_session() as s:
        user = _seed_user(s)
        item_id = _seed_catalog_item(s, user=user)
        handle = prepare(
            s, user, capability_name="update_catalog_item",
            raw_inputs={"item_id": str(item_id), "name": "agent name"},
        )
        commit(s, user, id=handle.id, edited_fields={"name": "human edited"})
        s.expire_all()
        assert s.get(CatalogItem, item_id).name == "human edited"


def test_update_catalog_item_edit_locked_field_rejected():
    with _rollback_session() as s:
        user = _seed_user(s)
        item_id = _seed_catalog_item(s, user=user)
        handle = prepare(
            s, user, capability_name="update_catalog_item",
            raw_inputs={"item_id": str(item_id), "name": "x"},
        )
        try:
            commit(s, user, id=handle.id, edited_fields={"item_id": str(uuid4())})
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "item_id" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for locked-field edit")


def test_update_catalog_item_cross_tenant_rejected():
    with _rollback_session() as s:
        user_a = _seed_user(s, label="A")
        item_a = _seed_catalog_item(s, user=user_a)
        user_b = _seed_user(s, label="B")
        try:
            prepare(
                s, user_b, capability_name="update_catalog_item",
                raw_inputs={"item_id": str(item_a), "name": "hijack"},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "not found" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT cross-tenant")


def test_update_catalog_item_no_editable_fields_rejected_at_prepare():
    with _rollback_session() as s:
        user = _seed_user(s)
        item_id = _seed_catalog_item(s, user=user)
        try:
            prepare(
                s, user, capability_name="update_catalog_item",
                raw_inputs={"item_id": str(item_id)},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "no fields to update" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT for empty patch")


# ===========================================================================
# deactivate_catalog_item
# ===========================================================================

def test_deactivate_catalog_item_prepare_returns_preview():
    with _rollback_session() as s:
        user = _seed_user(s)
        item_id = _seed_catalog_item(s, user=user, name="Old Item")
        handle = prepare(
            s, user, capability_name="deactivate_catalog_item",
            raw_inputs={"item_id": str(item_id)},
        )
        assert "Old Item" in handle.preview
        assert "is_active=false" in handle.preview
        assert handle.editable_fields == ()


def test_deactivate_catalog_item_commit_flips_is_active():
    with _rollback_session() as s:
        user = _seed_user(s)
        item_id = _seed_catalog_item(s, user=user)
        handle = prepare(
            s, user, capability_name="deactivate_catalog_item",
            raw_inputs={"item_id": str(item_id)},
        )
        result = commit(s, user, id=handle.id)
        assert result["is_active"] is False
        s.expire_all()
        row = s.get(CatalogItem, item_id)
        assert row.is_active is False


def test_deactivate_catalog_item_second_commit_rejected():
    with _rollback_session() as s:
        user = _seed_user(s)
        item_id = _seed_catalog_item(s, user=user)
        handle = prepare(
            s, user, capability_name="deactivate_catalog_item",
            raw_inputs={"item_id": str(item_id)},
        )
        commit(s, user, id=handle.id)
        try:
            commit(s, user, id=handle.id)
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.PREPARED_ACTION_UNAVAILABLE
        else:
            raise AssertionError("expected PREPARED_ACTION_UNAVAILABLE")


def test_deactivate_catalog_item_cross_tenant_rejected():
    with _rollback_session() as s:
        user_a = _seed_user(s, label="A")
        item_a = _seed_catalog_item(s, user=user_a)
        user_b = _seed_user(s, label="B")
        try:
            prepare(
                s, user_b, capability_name="deactivate_catalog_item",
                raw_inputs={"item_id": str(item_a)},
            )
        except WriteSurfaceError as exc:
            assert exc.code == ErrorCode.INVALID_INPUT
            assert "not found" in exc.message
        else:
            raise AssertionError("expected INVALID_INPUT cross-tenant")


def test_deactivate_catalog_item_already_inactive_preview_noop():
    with _rollback_session() as s:
        user = _seed_user(s)
        item_id = _seed_catalog_item(s, user=user, is_active=False)
        handle = prepare(
            s, user, capability_name="deactivate_catalog_item",
            raw_inputs={"item_id": str(item_id)},
        )
        assert "already inactive" in handle.preview
        # Still valid to commit (idempotent no-op at the service)
        commit(s, user, id=handle.id)
        s.expire_all()
        assert s.get(CatalogItem, item_id).is_active is False


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
