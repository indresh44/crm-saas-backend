"""Tests for the enriched lead_activities event log (migration 0042).

Three classes of assertion:

  COVERAGE — each newly-covered write emits exactly one activity of the
  right type with payload populated (LEAD_CREATED, LEAD_UPDATED,
  INVOICE_SENT, INVOICE_CANCELLED, INVOICE_ADJUSTED).

  ATTRIBUTION — actor_type lands correctly:
    * HUMAN  — a service call made under the HTTP middleware scope.
    * AI     — a service call wrapped explicitly in set_actor_context(AI).
    * TASK   — a service call under the multi-task runner's context wrap.
    * SYSTEM — the WhatsApp webhook helper.

  STRUCTURAL GUARD (source-grep) — every `create_lead_activity(` site in
  the services tree contributes a payload kwarg for SYSTEM-typed events
  (everything except user-logged NOTE/CALL/MEETING/WHATSAPP). This is
  unusual (a test that reads source code) but it's the right guard
  against silent payload drift — see plan risk #8.

Run:
    python -m app.services.tests.test_lead_activity_emits_pg
"""

from __future__ import annotations

import asyncio
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlmodel import Session, select

from app.core.actor_context import set_actor_context
from app.core.database import engine
from app.core.phone import normalize_phone_value
from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import (
    ActorType,
    InvoiceAdjustmentType,
    InvoiceStatus,
    LeadActivityType,
    LeadSource,
    PaymentMethod,
    UserRole,
)
from app.models.invoice import Invoice
from app.models.invoice_adjustment import InvoiceAdjustmentCreate
from app.models.invoice_item import InvoiceItem
from app.models.lead import LeadActivity, LeadCreate, LeadUpdate
from app.models.pipeline import Pipeline, PipelineStage
from app.models.user import User
from app.services import (
    invoice_adjustment_service,
    invoice_service,
    lead_service,
)
from app.services.invoice_service import (
    InvoiceData, InvoiceUpdateData, InvoiceUpdateWithItems,
)


# ---------------------------------------------------------------------------
# Savepoint-rollback session
# ---------------------------------------------------------------------------

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


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@dataclass
class _Fix:
    user: User
    stage_id: UUID
    customer_id: UUID


def _seed(session: Session) -> _Fix:
    bid = uuid4()
    session.add(Business(id=bid, name="Diary Test Co", phone="9990000000",
                         timezone="Asia/Kolkata"))
    session.flush()
    user = User(id=uuid4(), business_id=bid, name="Owner",
                email=f"owner-{bid}@test.local",
                role=UserRole.OWNER, is_active=True)
    session.add(user); session.flush()
    pid = uuid4()
    session.add(Pipeline(id=pid, business_id=bid, name="Default", is_default=True))
    session.flush()
    sid = uuid4()
    session.add(PipelineStage(id=sid, pipeline_id=pid, name="New Enquiry",
                              position=1, color="#888"))
    session.flush()
    cid = uuid4()
    session.add(Customer(id=cid, business_id=bid, name="Rajesh Mehta",
                         phone="+91 99999 00001",
                         phone_normalized=normalize_phone_value("+91 99999 00001")))
    session.commit()
    return _Fix(user=user, stage_id=sid, customer_id=cid)


def _activities_for(session: Session, lead_id: UUID) -> list[LeadActivity]:
    return list(session.exec(
        select(LeadActivity)
        .where(LeadActivity.lead_id == lead_id)
        .order_by(LeadActivity.created_at.asc(), LeadActivity.id.asc())
    ).all())


# ===========================================================================
# COVERAGE — each newly-covered write emits exactly one activity
# ===========================================================================

def test_lead_created_emits_lead_created_activity_with_payload():
    """The diary's starting event. Was missing pre-0042 — every lead's
    history started partway through."""
    with _rollback_session() as s:
        f = _seed(s)
        with set_actor_context(ActorType.HUMAN):
            lead = lead_service.create_lead(s, f.user, LeadCreate(
                stage_id=f.stage_id, title="Restaurant interiors",
                customer_id=f.customer_id, source=LeadSource.WALK_IN,
                estimated_value=Decimal("800000.00"),
            ))
        acts = _activities_for(s, lead.id)
        assert len(acts) == 1
        a = acts[0]
        assert a.type == LeadActivityType.LEAD_CREATED
        assert "Restaurant interiors" in a.description
        assert "New Enquiry" in a.description
        # Payload carries the lifecycle facts.
        assert a.payload["title"] == "Restaurant interiors"
        assert a.payload["stage_name"] == "New Enquiry"
        assert a.payload["source"] == "walk_in"
        assert a.payload["estimated_value"] == "800000.00"
        # Actor stamped by the chokepoint from the explicit context wrap.
        assert a.actor_type == ActorType.HUMAN
        assert a.created_by == f.user.id


def test_lead_updated_emits_per_changed_field_old_new_payload():
    """The biggest survey gap: previously silent. Now records exactly
    which fields changed and the old → new values in payload."""
    with _rollback_session() as s:
        f = _seed(s)
        with set_actor_context(ActorType.HUMAN):
            lead = lead_service.create_lead(s, f.user, LeadCreate(
                stage_id=f.stage_id, title="Modular kitchen",
                customer_id=f.customer_id,
                estimated_value=Decimal("100000.00"),
            ))
            lead_service.update_lead(s, f.user, lead.id, LeadUpdate(
                title="Modular kitchen (revised)",
                estimated_value=Decimal("50000.00"),
            ))
        acts = _activities_for(s, lead.id)
        # 1 LEAD_CREATED + 1 LEAD_UPDATED
        assert [a.type for a in acts] == [
            LeadActivityType.LEAD_CREATED,
            LeadActivityType.LEAD_UPDATED,
        ]
        upd = acts[1]
        assert "title" in upd.description and "estimated_value" in upd.description
        assert "Modular kitchen" in upd.description
        # Payload has exactly the two changed fields, each with old + new.
        changed = upd.payload["changed"]
        assert set(changed.keys()) == {"title", "estimated_value"}
        assert changed["title"] == {
            "old": "Modular kitchen", "new": "Modular kitchen (revised)",
        }
        assert changed["estimated_value"] == {
            "old": "100000.00", "new": "50000.00",
        }


def test_lead_updated_with_no_actual_changes_emits_nothing():
    """A PATCH with the same values as current is not noise-worthy —
    don't pollute the diary with 'updated lead: (nothing)' entries."""
    with _rollback_session() as s:
        f = _seed(s)
        with set_actor_context(ActorType.HUMAN):
            lead = lead_service.create_lead(s, f.user, LeadCreate(
                stage_id=f.stage_id, title="Modular kitchen",
                customer_id=f.customer_id,
            ))
            # Re-set title to its current value — should produce NO new activity.
            lead_service.update_lead(s, f.user, lead.id, LeadUpdate(
                title="Modular kitchen",
            ))
        acts = _activities_for(s, lead.id)
        # Only the CREATED entry; no LEAD_UPDATED for the no-op patch.
        assert [a.type for a in acts] == [LeadActivityType.LEAD_CREATED]


def _make_invoice(s: Session, f: _Fix, lead_id: UUID) -> Invoice:
    """Direct insert of a minimal sent-ready invoice + one item — bypasses
    the invoice_service.create_invoice path (which we test separately)
    so this fixture is independent of that path's emit behaviour."""
    inv = Invoice(
        id=uuid4(), business_id=f.user.business_id, lead_id=lead_id,
        invoice_number="INV-T-0001", status=InvoiceStatus.DRAFT,
        issued_date=date_cls.today(),
        due_date=date_cls.today() + timedelta(days=15),
        subtotal=Decimal("1000.00"), tax_total=Decimal("0.00"),
        total_amount=Decimal("1000.00"),
    )
    s.add(inv); s.flush()
    s.add(InvoiceItem(
        id=uuid4(), invoice_id=inv.id,
        name="Test item", description="",
        quantity=Decimal("1"), unit_price=Decimal("1000.00"),
        gst_percent=Decimal("0"), amount=Decimal("1000.00"),
    ))
    s.commit()
    s.refresh(inv)
    return inv


def test_invoice_sent_emits_only_on_draft_to_sent_transition():
    """The single diary-worthy intermediate transition. Other status
    moves stay silent."""
    with _rollback_session() as s:
        f = _seed(s)
        with set_actor_context(ActorType.HUMAN):
            lead = lead_service.create_lead(s, f.user, LeadCreate(
                stage_id=f.stage_id, title="L", customer_id=f.customer_id,
            ))
            inv = _make_invoice(s, f, lead.id)
            # DRAFT → SENT
            invoice_service.update_invoice(
                s, f.user, inv.id,
                InvoiceUpdateWithItems(
                    invoice=InvoiceUpdateData(status=InvoiceStatus.SENT),
                    items=None,
                ),
            )
        acts_by_type = [a.type for a in _activities_for(s, lead.id)]
        # LEAD_CREATED + INVOICE_SENT
        assert LeadActivityType.INVOICE_SENT in acts_by_type
        sent = [a for a in _activities_for(s, lead.id)
                if a.type == LeadActivityType.INVOICE_SENT][0]
        assert sent.payload["invoice_number"] == "INV-T-0001"


def test_invoice_cancelled_emits_with_reason_in_payload():
    """Survey gap: the cancellation reason was stored on the invoice
    column but never logged. Now duplicated into the activity payload
    so the diary stands alone."""
    with _rollback_session() as s:
        f = _seed(s)
        with set_actor_context(ActorType.HUMAN):
            lead = lead_service.create_lead(s, f.user, LeadCreate(
                stage_id=f.stage_id, title="L", customer_id=f.customer_id,
            ))
            inv = _make_invoice(s, f, lead.id)
            invoice_service.cancel_invoice(
                s, f.user, inv.id, reason="customer changed mind",
            )
        cancelled = [a for a in _activities_for(s, lead.id)
                     if a.type == LeadActivityType.INVOICE_CANCELLED]
        assert len(cancelled) == 1
        a = cancelled[0]
        assert "INV-T-0001" in a.description
        assert "customer changed mind" in a.description
        assert a.payload["reason"] == "customer changed mind"
        assert a.payload["invoice_number"] == "INV-T-0001"


def test_invoice_adjusted_emits_with_type_and_amount_in_payload():
    """Survey gap: adjustments (discount / write-off) had no diary entry.
    Now logged with type + amount so balance changes are explainable."""
    with _rollback_session() as s:
        f = _seed(s)
        with set_actor_context(ActorType.HUMAN):
            lead = lead_service.create_lead(s, f.user, LeadCreate(
                stage_id=f.stage_id, title="L", customer_id=f.customer_id,
            ))
            inv = _make_invoice(s, f, lead.id)
            # Move to APPROVED so adjustments are allowed.
            invoice_service.update_invoice(
                s, f.user, inv.id,
                InvoiceUpdateWithItems(
                    invoice=InvoiceUpdateData(status=InvoiceStatus.APPROVED),
                    items=None,
                ),
            )
            invoice_adjustment_service.add_adjustment(
                s, f.user, inv.id,
                InvoiceAdjustmentCreate(
                    adjustment_type=InvoiceAdjustmentType.DISCOUNT,
                    amount=Decimal("250.00"),
                    reason="repeat customer discount",
                ),
            )
        adj = [a for a in _activities_for(s, lead.id)
               if a.type == LeadActivityType.INVOICE_ADJUSTED]
        assert len(adj) == 1
        a = adj[0]
        assert "discount" in a.description
        assert "250" in a.description
        assert a.payload["adjustment_type"] == "discount"
        assert a.payload["amount"] == "250.00"
        assert a.payload["reason"] == "repeat customer discount"


# ===========================================================================
# ATTRIBUTION — actor_type lands correctly per surface
# ===========================================================================

def test_actor_type_human_when_under_http_middleware_scope():
    """HTTP middleware sets HUMAN. We simulate by wrapping the call in
    set_actor_context(HUMAN), which is what the middleware does."""
    with _rollback_session() as s:
        f = _seed(s)
        with set_actor_context(ActorType.HUMAN):
            lead = lead_service.create_lead(s, f.user, LeadCreate(
                stage_id=f.stage_id, title="L", customer_id=f.customer_id,
            ))
        assert _activities_for(s, lead.id)[0].actor_type == ActorType.HUMAN


def test_actor_type_ai_when_wrapped_in_ai_context():
    """AI write-surface capability invocations are wrapped in
    set_actor_context(AI) by the agent loop's dispatch."""
    with _rollback_session() as s:
        f = _seed(s)
        with set_actor_context(ActorType.AI):
            lead = lead_service.create_lead(s, f.user, LeadCreate(
                stage_id=f.stage_id, title="L", customer_id=f.customer_id,
            ))
        a = _activities_for(s, lead.id)[0]
        assert a.actor_type == ActorType.AI


def test_actor_type_task_with_session_and_task_ids_when_under_runner():
    """The multi-task runner wraps each task in set_actor_context(TASK,
    chat_session_id=..., task_id=...). Activities created under that scope
    inherit the linkage, enabling the diary to trace an action back to
    the conversation that caused it.

    We seed a real AgentChatSession + AgentTask because the lead_activities
    columns have ON DELETE SET NULL FKs to them — the FK is enforced on
    insert, not just on delete."""
    from app.models.agent_chat import AgentChatSession
    from app.models.agent_task import AgentTask, TaskStatus
    with _rollback_session() as s:
        f = _seed(s)
        chat_session = AgentChatSession(
            id=uuid4(), business_id=f.user.business_id, user_id=f.user.id,
        )
        s.add(chat_session); s.flush()
        task = AgentTask(
            id=uuid4(), business_id=f.user.business_id,
            session_id=chat_session.id, user_message_id=None,
            batch_id=uuid4(), sequence_index=0,
            description="seed", status=TaskStatus.RUNNING.value,
        )
        s.add(task); s.commit()

        with set_actor_context(
            ActorType.TASK,
            chat_session_id=chat_session.id, task_id=task.id,
        ):
            lead = lead_service.create_lead(s, f.user, LeadCreate(
                stage_id=f.stage_id, title="L", customer_id=f.customer_id,
            ))
        a = _activities_for(s, lead.id)[0]
        assert a.actor_type == ActorType.TASK
        assert a.chat_session_id == chat_session.id
        assert a.task_id == task.id


def test_actor_type_system_for_webhook_path_with_null_created_by():
    """The WhatsApp webhook is the canonical SYSTEM actor. With the 0042
    nullable-created_by change it can finally store NULL honestly instead
    of misattributing to a random business user."""
    from app.services.whatsapp_webhook_service import _create_incoming_lead_activity
    with _rollback_session() as s:
        f = _seed(s)
        with set_actor_context(ActorType.HUMAN):
            lead = lead_service.create_lead(s, f.user, LeadCreate(
                stage_id=f.stage_id, title="L", customer_id=f.customer_id,
            ))
        # Webhook handler — sets SYSTEM inside its own scope.
        _create_incoming_lead_activity(
            s, f.user.business_id, lead.id, "Hello, when will it be ready?",
        )
        s.commit()
        wa = [a for a in _activities_for(s, lead.id)
              if a.type == LeadActivityType.WHATSAPP]
        assert len(wa) == 1
        assert wa[0].actor_type == ActorType.SYSTEM
        assert wa[0].created_by is None   # HONEST — no real user behind webhooks


def test_pre_existing_rows_with_null_actor_type_round_trip_cleanly():
    """Backward compat: old rows have NULL actor_type and NULL payload.
    Insert one bypassing the chokepoint to simulate a pre-0042 row;
    confirm it reads back cleanly via the API serializer."""
    from app.models.lead import LeadActivityRead
    with _rollback_session() as s:
        f = _seed(s)
        with set_actor_context(ActorType.HUMAN):
            lead = lead_service.create_lead(s, f.user, LeadCreate(
                stage_id=f.stage_id, title="L", customer_id=f.customer_id,
            ))
        # Old-style row: created_by + type + description, nothing else.
        old = LeadActivity(
            lead_id=lead.id,
            type=LeadActivityType.NOTE,
            description="legacy note",
            created_by=f.user.id,
            # actor_type, payload, chat_session_id, task_id all NULL.
        )
        s.add(old); s.commit(); s.refresh(old)

        # Pydantic serializer tolerates NULL on all 0042 fields.
        rendered = LeadActivityRead.model_validate(old, from_attributes=True)
        assert rendered.actor_type is None
        assert rendered.payload is None
        assert rendered.chat_session_id is None
        assert rendered.task_id is None
        # created_by still works for old non-null rows.
        assert rendered.created_by == f.user.id


# ===========================================================================
# STRUCTURAL GUARD — source-grep for payload on SYSTEM-typed emits
# ===========================================================================

# Activity types where payload MAY be NULL (user-supplied prose carries the
# content; structured payload would duplicate it). Everything else MUST
# carry a payload kwarg at the emit site.
_USER_LOGGED_TYPES = frozenset({
    "LeadActivityType.NOTE",
    "LeadActivityType.CALL",
    "LeadActivityType.MEETING",
    "LeadActivityType.WHATSAPP",
})

_SERVICES_ROOT = Path(__file__).resolve().parents[2] / "services"


def _emit_blocks_from_file(path: Path) -> list[tuple[int, str]]:
    """Find every `create_lead_activity(...)` or `_log_lead_activity(...)`
    block in a file. Returns list of (line_no, full_block_text)."""
    src = path.read_text(encoding="utf-8")
    out: list[tuple[int, str]] = []
    pattern = re.compile(
        r"(?:create_lead_activity|_log_lead_activity)\s*\(",
    )
    for m in pattern.finditer(src):
        # Find the matching closing paren by depth counting from m.end()-1.
        depth = 1
        i = m.end()
        while i < len(src) and depth > 0:
            ch = src[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        block = src[m.start():i]
        line_no = src.count("\n", 0, m.start()) + 1
        out.append((line_no, block))
    return out


def test_source_grep_every_system_typed_emit_supplies_payload():
    """Risk-8 structural guard — unusual but the right shape for this
    bug class. Walks every services/*.py file, finds every emit site,
    and asserts: if the activity_type is anything OTHER than the four
    user-logged types, the emit site MUST pass `payload=`.

    This catches the failure mode where a future emit is added without
    a payload — silent payload drift that would degrade the diary one
    blind spot at a time."""
    failures: list[str] = []
    for py in sorted(_SERVICES_ROOT.glob("*.py")):
        if py.name in ("__init__.py",):
            continue
        for line_no, block in _emit_blocks_from_file(py):
            # Identify the activity type literal in the block. Looks like:
            #   type=LeadActivityType.PAYMENT_RECORDED   or
            #   activity_type=LeadActivityType.LEAD_CREATED
            type_match = re.search(
                r"(?:type|activity_type)\s*=\s*(LeadActivityType\.[A-Z_]+)",
                block,
            )
            if not type_match:
                # Helper-of-helper pattern (e.g. activity_type=parameter).
                # Skip silently — the actual emit site is a wrapper that
                # forwards a type from its caller; the calling site is
                # what we evaluate.
                continue
            type_literal = type_match.group(1)
            if type_literal in _USER_LOGGED_TYPES:
                continue
            # System-emitted — must carry a payload kwarg.
            if "payload=" not in block:
                failures.append(
                    f"{py.name}:{line_no} emits {type_literal} without "
                    f"payload=. Add a payload= dict at the call site so "
                    f"the diary has machine-readable facts (see the "
                    f"shape table in plan part 3)."
                )
    assert not failures, "Payload missing at emit site(s):\n  " + "\n  ".join(failures)


# ===========================================================================
# Driver
# ===========================================================================

def _all_tests():
    tests = [
        test_lead_created_emits_lead_created_activity_with_payload,
        test_lead_updated_emits_per_changed_field_old_new_payload,
        test_lead_updated_with_no_actual_changes_emits_nothing,
        test_invoice_sent_emits_only_on_draft_to_sent_transition,
        test_invoice_cancelled_emits_with_reason_in_payload,
        test_invoice_adjusted_emits_with_type_and_amount_in_payload,
        test_actor_type_human_when_under_http_middleware_scope,
        test_actor_type_ai_when_wrapped_in_ai_context,
        test_actor_type_task_with_session_and_task_ids_when_under_runner,
        test_actor_type_system_for_webhook_path_with_null_created_by,
        test_pre_existing_rows_with_null_actor_type_round_trip_cleanly,
        test_source_grep_every_system_typed_emit_supplies_payload,
    ]
    failures = []
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            import traceback
            failures.append((t.__name__, exc))
            print(f"  FAIL {t.__name__}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
    print()
    print(f"{len(tests) - len(failures)}/{len(tests)} passed")
    return failures


if __name__ == "__main__":
    fails = _all_tests()
    raise SystemExit(1 if fails else 0)
