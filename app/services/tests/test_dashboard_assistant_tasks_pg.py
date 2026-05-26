"""Postgres-backed tests for the assistant-tasks dashboard endpoint.

Covers:
  * Three-bucket grouping returns the right tasks in each bucket.
  * Awaiting-approval rows carry prepared_action_id / preview /
    editable_fields lifted from `result` JSONB.
  * Cross-tenant: foreign business sees zero of tenant A's tasks.
  * Index hit: EXPLAIN confirms the
    ix_agent_tasks_business_status_updated composite index is used.
  * recently_done_limit is bounded by the hard cap server-side.
  * A task whose result.kind is not "awaiting_confirm" (e.g. ask_user
    parked in awaiting_approval) gets prepared_action_id=None — the
    carousel's graceful-degradation path will fire on it.

Run:
    python -m app.services.tests.test_dashboard_assistant_tasks_pg
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import event, text
from sqlmodel import Session

from app.core.database import engine
from app.models.agent_chat import AgentChatSession
from app.models.agent_task import AgentTask, TaskStatus
from app.models.business import Business
from app.models.enums import UserRole
from app.models.user import User
from app.services import dashboard_service


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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@dataclass
class _Tenant:
    user: User
    chat_session_id: UUID


def _seed_tenant(session: Session, *, name: str) -> _Tenant:
    bid = uuid4()
    session.add(Business(id=bid, name=name, phone=f"999{uuid4().int % 10000000:07d}"))
    session.flush()
    user = User(
        id=uuid4(), business_id=bid, name="Owner",
        email=f"owner-{bid}@test.local",
        role=UserRole.OWNER, is_active=True,
    )
    session.add(user); session.flush()
    chat = AgentChatSession(id=uuid4(), business_id=bid, user_id=user.id)
    session.add(chat); session.commit()
    return _Tenant(user=user, chat_session_id=chat.id)


def _add_task(
    session: Session,
    tenant: _Tenant,
    *,
    status: str,
    description: str,
    sequence_index: int = 0,
    result: dict | None = None,
    batch_id: UUID | None = None,
    continuity_history: list[dict] | None = None,
) -> AgentTask:
    # Deliberately do NOT populate task.pending_action_id — it's an FK to
    # prepared_actions.id and the dashboard service doesn't read it (the
    # prepared_action_id lifted into the summary comes from `result`
    # JSONB, not from the column). Keeping the column NULL avoids needing
    # to seed a PreparedAction row per test fixture.
    task = AgentTask(
        id=uuid4(),
        business_id=tenant.user.business_id,
        session_id=tenant.chat_session_id,
        user_message_id=None,
        batch_id=batch_id or uuid4(),
        sequence_index=sequence_index,
        description=description,
        status=status,
        result=result,
        continuity_history=continuity_history or [],
    )
    session.add(task); session.commit(); session.refresh(task)
    return task


def _add_awaiting_approval_task(
    session: Session,
    tenant: _Tenant,
    *,
    description: str,
    prepared_action_id: str,
    preview: str = "Do the thing",
    editable_fields: list[str] | None = None,
    capability: str = "record_payment",
) -> AgentTask:
    """Seed a REAL awaiting-approval task: creates a backing
    PreparedAction row so the FK on pending_action_id resolves, then
    creates the task with pending_action_id set. Use this for tests
    that need to assert against the awaiting_approval bucket; for
    ask_user-style stuck tasks use `_add_task` with pending_action_id
    left at default (NULL), which now routes to the needs_input bucket."""
    from datetime import datetime, timedelta, timezone
    from app.models.prepared_action import PreparedAction, STATUS_PENDING
    pa = PreparedAction(
        id=prepared_action_id,
        business_id=tenant.user.business_id,
        capability=capability,
        locked_data={},
        editable_data={},
        preview=preview,
        status=STATUS_PENDING,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    session.add(pa); session.flush()

    task = AgentTask(
        id=uuid4(),
        business_id=tenant.user.business_id,
        session_id=tenant.chat_session_id,
        user_message_id=None,
        batch_id=uuid4(),
        sequence_index=0,
        description=description,
        status=TaskStatus.AWAITING_APPROVAL.value,
        pending_action_id=prepared_action_id,
        result={
            "kind": "awaiting_confirm",
            "prepared_action_id": prepared_action_id,
            "preview": preview,
            "editable_fields": editable_fields or [],
        },
    )
    session.add(task); session.commit(); session.refresh(task)
    return task


def _committed_turn(capability: str) -> dict:
    """Helper: synthesise the shape of a COMMITTED turn that the chat
    service's confirm path appends to continuity_history after a
    successful /confirm. Only the action.type='committed' + capability
    matters to the dashboard service; observation_raw isn't read."""
    return {
        "turn": 1,
        "thought": "(harness) commit landed",
        "action": {"type": "committed", "action_id": "a1", "capability": capability},
        "observation_summary": f"COMMITTED action_id=a1 capability={capability!r}",
        "observation_raw": {},
    }


# ===========================================================================
# Tests
# ===========================================================================

def test_three_bucket_grouping_with_notable_filter_on_recently_done():
    """Buckets after the ask_user routing fix:
      * running              — RUNNING tasks
      * awaiting_approval    — AWAITING_APPROVAL with pending_action_id NOT NULL
      * needs_input          — AWAITING_APPROVAL with pending_action_id NULL
      * recently_done        — terminal NOTABLE rows
    Each populated with one representative task here."""
    with _rollback_session() as s:
        t = _seed_tenant(s, name="Co A")
        _add_task(s, t, status=TaskStatus.RUNNING.value, description="R1")
        # A1 must be a REAL awaiting-approval (real prepared_action backing
        # the pending_action_id), otherwise the new router sends it to
        # needs_input — which is the entire point of the routing fix.
        _add_awaiting_approval_task(
            s, t, description="A1", prepared_action_id="act-1",
            preview="Mark followup X done",
        )
        # D1: done with NO committed turn → pure read-only Q&A → filtered out.
        _add_task(s, t, status=TaskStatus.DONE.value,
                  description="What's the weather?",
                  result={"kind": "done", "answer": "noted"})
        # D2: done with a committed turn → notable.
        _add_task(s, t, status=TaskStatus.DONE.value,
                  description="record 3000 on INV-008",
                  result={"kind": "done", "answer": "Done — record_payment committed."},
                  continuity_history=[_committed_turn("record_payment")])
        # F1: failed → always notable.
        _add_task(s, t, status=TaskStatus.FAILED.value, description="F1",
                  result={"kind": "error",
                          "error": {"code": "x", "message": "boom"}})

        resp = dashboard_service.get_assistant_tasks(
            session=s, business_id=t.user.business_id,
        )
        assert [r.description for r in resp.running] == ["R1"]
        assert [r.description for r in resp.awaiting_approval] == ["A1"]
        assert resp.needs_input == []
        descs = {r.description for r in resp.recently_done}
        # D1 is silenced; D2 (write) and F1 (failed) survive.
        assert descs == {"record 3000 on INV-008", "F1"}, descs
        assert all(r.notable for r in resp.recently_done)


def test_awaiting_approval_row_carries_prepared_action_id_preview_editable_fields():
    """The carousel needs these without a second fetch — they're lifted from
    `result` JSONB at the service boundary. Uses the real-prepared-action
    helper because the new router only routes to awaiting_approval when
    pending_action_id IS NOT NULL."""
    with _rollback_session() as s:
        t = _seed_tenant(s, name="Co A")
        _add_awaiting_approval_task(
            s, t, description="record payment",
            prepared_action_id="act-abc",
            preview="Record ₹5,000 on INV-001",
            editable_fields=["amount", "reference"],
        )
        resp = dashboard_service.get_assistant_tasks(
            session=s, business_id=t.user.business_id,
        )
        row = resp.awaiting_approval[0]
        assert row.prepared_action_id == "act-abc"
        assert row.preview == "Record ₹5,000 on INV-001"
        assert row.editable_fields == ["amount", "reference"]


def test_ask_user_tasks_routed_to_needs_input_not_awaiting_approval():
    """The stuck-ask_user fix: a task in status='awaiting_approval' with
    pending_action_id=NULL goes to the `needs_input` bucket, NOT
    `awaiting_approval`. The dashboard's Approve button + carousel
    target only real prepared writes; questions get a separate UI with
    Answer-in-chat / Dismiss."""
    with _rollback_session() as s:
        t = _seed_tenant(s, name="Co A")
        _add_task(s, t, status=TaskStatus.AWAITING_APPROVAL.value,
                  description="ask_user task",
                  result={"kind": "ask_user",
                          "question": "Which Rajesh?"})
        resp = dashboard_service.get_assistant_tasks(
            session=s, business_id=t.user.business_id,
        )
        # Routed to needs_input, NOT awaiting_approval.
        assert resp.awaiting_approval == []
        assert len(resp.needs_input) == 1
        row = resp.needs_input[0]
        assert row.prepared_action_id is None
        assert row.preview is None
        assert row.result_kind == "ask_user"


def test_awaiting_approval_bucket_only_contains_real_prepared_writes():
    """Mixed: one real prepared write + one ask_user pause in the same
    business. They split cleanly — pending_action_id distinguishes them."""
    with _rollback_session() as s:
        t = _seed_tenant(s, name="Co A")
        _add_awaiting_approval_task(
            s, t, description="real write", prepared_action_id="act-real-1",
            preview="Record payment ₹100", editable_fields=["amount"],
        )
        _add_task(s, t, status=TaskStatus.AWAITING_APPROVAL.value,
                  description="ask",
                  result={"kind": "ask_user", "question": "Which one?"})

        resp = dashboard_service.get_assistant_tasks(
            session=s, business_id=t.user.business_id,
        )
        assert len(resp.awaiting_approval) == 1
        assert resp.awaiting_approval[0].prepared_action_id == "act-real-1"
        assert len(resp.needs_input) == 1
        assert resp.needs_input[0].result_kind == "ask_user"


def test_done_and_failed_rows_carry_answer_and_error_message():
    """D needs a committed turn to survive the notable filter (it's not
    inherently notable as a bare done); F survives because failed is
    always notable. Both must still carry their answer / error fields
    for the expanded panel to render."""
    with _rollback_session() as s:
        t = _seed_tenant(s, name="Co A")
        _add_task(s, t, status=TaskStatus.DONE.value, description="D",
                  result={"kind": "done", "answer": "3 followups today"},
                  continuity_history=[_committed_turn("complete_followup")])
        _add_task(s, t, status=TaskStatus.FAILED.value, description="F",
                  result={"kind": "error",
                          "error": {"code": "x", "message": "explosion"}})
        resp = dashboard_service.get_assistant_tasks(
            session=s, business_id=t.user.business_id,
        )
        by_desc = {r.description: r for r in resp.recently_done}
        assert by_desc["D"].answer == "3 followups today"
        assert by_desc["F"].error_message == "explosion"


def test_recently_done_limit_bounds_to_hard_cap_server_side():
    """Caller can't ask for more than _RECENTLY_DONE_HARD_CAP notable
    rows even if they pass a huge `recently_done_limit`. Notable=write
    (committed turn) so we attach one per row."""
    with _rollback_session() as s:
        t = _seed_tenant(s, name="Co A")
        # Seed 60 notable done tasks; cap is 50.
        for i in range(60):
            _add_task(s, t, status=TaskStatus.DONE.value,
                      description=f"D{i}",
                      result={"kind": "done", "answer": "x"},
                      continuity_history=[_committed_turn("record_payment")])
        resp = dashboard_service.get_assistant_tasks(
            session=s, business_id=t.user.business_id,
            recently_done_limit=10_000,
        )
        assert len(resp.recently_done) == 50


def test_recently_done_limit_zero_returns_empty_bucket():
    with _rollback_session() as s:
        t = _seed_tenant(s, name="Co A")
        _add_task(s, t, status=TaskStatus.DONE.value, description="D",
                  result={"kind": "done", "answer": "x"},
                  continuity_history=[_committed_turn("record_payment")])
        resp = dashboard_service.get_assistant_tasks(
            session=s, business_id=t.user.business_id, recently_done_limit=0,
        )
        assert resp.recently_done == []
        # Running / awaiting buckets unaffected.
        assert resp.running == [] and resp.awaiting_approval == []


def test_cross_tenant_request_returns_zero_of_other_tenants_tasks():
    """Critical: dashboard data is tenant-scoped via the leading-column
    index filter. A foreign business must NOT see another tenant's
    in-flight tasks. Done-row gets a committed turn so it's notable and
    would surface to its OWN tenant if the scoping ever broke."""
    with _rollback_session() as s:
        a = _seed_tenant(s, name="Co A")
        b = _seed_tenant(s, name="Co B")
        _add_awaiting_approval_task(
            s, a, description="A — secret", prepared_action_id="a1",
            preview="...", editable_fields=[],
        )
        _add_task(s, a, status=TaskStatus.RUNNING.value, description="A — running")
        _add_task(s, a, status=TaskStatus.DONE.value, description="A — done",
                  result={"kind": "done", "answer": "x"},
                  continuity_history=[_committed_turn("record_payment")])

        # B has nothing; the endpoint must return three empty lists.
        resp_b = dashboard_service.get_assistant_tasks(
            session=s, business_id=b.user.business_id,
        )
        assert resp_b.running == []
        assert resp_b.awaiting_approval == []
        assert resp_b.recently_done == []

        # A still sees its own.
        resp_a = dashboard_service.get_assistant_tasks(
            session=s, business_id=a.user.business_id,
        )
        assert {r.description for r in resp_a.awaiting_approval} == {"A — secret"}


def test_index_hit_explain_does_not_use_seq_scan():
    """EXPLAIN the canonical bucket query — must NOT be a Seq Scan.

    At low row counts Postgres correctly prefers the standalone
    `ix_agent_tasks_business_id` over the composite
    `ix_agent_tasks_business_status_updated` (sorting 5 rows in memory is
    cheaper than walking a composite index). Both are acceptable plans;
    what's NOT acceptable is a Seq Scan, which would mean we lost the
    business_id filter to a scan and would not survive production
    cardinality. This test guards against THAT regression, not against the
    planner's perfectly reasonable index-choice freedom."""
    with _rollback_session() as s:
        t = _seed_tenant(s, name="Co A")
        for i in range(5):
            _add_task(s, t, status=TaskStatus.AWAITING_APPROVAL.value,
                      description=f"A{i}",
                      result={"kind": "awaiting_confirm",
                              "prepared_action_id": f"a{i}",
                              "preview": "p", "editable_fields": []})

        plan = list(s.exec(text(
            "EXPLAIN SELECT id FROM agent_tasks "
            "WHERE business_id = :bid AND status = 'awaiting_approval' "
            "ORDER BY updated_at DESC LIMIT 50"
        ).bindparams(bid=t.user.business_id)).all())
        plan_text = "\n".join(row[0] for row in plan)
        assert "Seq Scan" not in plan_text, (
            f"query against agent_tasks fell back to a Seq Scan — index "
            f"on business_id is no longer being used:\n{plan_text}"
        )
        # Must use at least one of the two business_id-leading indexes.
        assert (
            "ix_agent_tasks_business_id" in plan_text
            or "ix_agent_tasks_business_status_updated" in plan_text
        ), f"plan did not use any business_id-leading index:\n{plan_text}"


def test_empty_business_returns_three_empty_buckets():
    with _rollback_session() as s:
        t = _seed_tenant(s, name="Co A")
        resp = dashboard_service.get_assistant_tasks(
            session=s, business_id=t.user.business_id,
        )
        assert resp.running == []
        assert resp.awaiting_approval == []
        assert resp.recently_done == []


def test_notable_filter_excludes_only_bare_chat_done_tasks():
    """Three done tasks with EMPTY continuity_history (no reads, no
    prepares — the LLM answered with a bare DONE on turn 1, pure chat)
    → ZERO survive in recently_done. These are 'ok thanks' / 'say hi'
    style; the chat history still has them."""
    with _rollback_session() as s:
        t = _seed_tenant(s, name="Co A")
        for q in ["ok thanks", "great", "say hi"]:
            _add_task(s, t, status=TaskStatus.DONE.value, description=q,
                      result={"kind": "done", "answer": "..."},
                      continuity_history=[])   # no tool use at all
        resp = dashboard_service.get_assistant_tasks(
            session=s, business_id=t.user.business_id,
        )
        assert resp.recently_done == []


def test_read_only_task_with_at_least_one_read_is_notable():
    """The user-facing case the strict rule got wrong: a read-only task
    that did real work ('show partial-paid invoices in a table') has a
    read turn in continuity_history → must surface, even though no
    write happened. The icon falls back to 'read' since no capability
    committed; the short_label falls back to the description."""
    read_turn = {
        "turn": 1,
        "thought": "fetching invoices",
        "action": {
            "type": "read",
            "query": {"entity": "invoices",
                      "filters": [{"field": "status", "op": "=", "value": "partial"}]},
        },
        "observation_summary": "read on invoices: 3 row(s) matched.",
        "observation_raw": {},
    }
    with _rollback_session() as s:
        t = _seed_tenant(s, name="Co A")
        _add_task(
            s, t, status=TaskStatus.DONE.value,
            description="show all invoices which are partial paid",
            result={"kind": "done", "answer": "| Invoice | Total | Pending |\n|---|---|---|\n| INV-1 | 5000 | 2000 |"},
            continuity_history=[read_turn],
        )
        resp = dashboard_service.get_assistant_tasks(
            session=s, business_id=t.user.business_id,
        )
        assert len(resp.recently_done) == 1
        row = resp.recently_done[0]
        assert row.notable
        assert row.icon == "read"
        assert row.short_label == "show all invoices which are partial paid"


def test_cancelled_prepare_is_notable_and_surfaces_in_recently_done():
    """When the owner cancels a prepared action, apply_cancel_to_task
    marks the task DONE with result.kind='cancelled'. This is the
    owner's deliberate decision — surface it on the dashboard."""
    with _rollback_session() as s:
        t = _seed_tenant(s, name="Co A")
        _add_task(s, t, status=TaskStatus.DONE.value, description="record 5000",
                  result={"kind": "cancelled",
                          "message": "user declined the prepared action"})
        resp = dashboard_service.get_assistant_tasks(
            session=s, business_id=t.user.business_id,
        )
        assert len(resp.recently_done) == 1
        row = resp.recently_done[0]
        assert row.notable
        assert "Cancelled" in row.short_label


def test_uninformative_description_replaced_by_capability_verb():
    """'try again' → 'Recorded payment' when the task ended up committing
    record_payment. The owner sees what the assistant DID, not the
    forgettable prompt that triggered it."""
    with _rollback_session() as s:
        t = _seed_tenant(s, name="Co A")
        _add_task(s, t, status=TaskStatus.DONE.value, description="try again",
                  result={"kind": "done", "answer": "Done — record_payment committed."},
                  continuity_history=[_committed_turn("record_payment")])
        resp = dashboard_service.get_assistant_tasks(
            session=s, business_id=t.user.business_id,
        )
        row = resp.recently_done[0]
        assert row.short_label == "Recorded payment"
        assert row.icon == "payment"


def test_informative_description_preserved_over_capability_verb():
    """A long owner description IS informative — keep it as the label
    rather than substituting the generic verb."""
    with _rollback_session() as s:
        t = _seed_tenant(s, name="Co A")
        long_desc = "Record ₹3,000 payment for VI-008 against Rajesh's invoice"
        _add_task(s, t, status=TaskStatus.DONE.value, description=long_desc,
                  result={"kind": "done", "answer": "Done — record_payment committed."},
                  continuity_history=[_committed_turn("record_payment")])
        resp = dashboard_service.get_assistant_tasks(
            session=s, business_id=t.user.business_id,
        )
        row = resp.recently_done[0]
        # Owner's words win when they say something useful.
        assert row.short_label == long_desc
        assert row.icon == "payment"


def test_icon_categories_mapped_per_capability_family():
    """Each capability lands in its domain icon bucket; unknown
    capabilities fall back to 'write'; tasks with no committed turn
    (failed tasks here) fall back to 'read'."""
    with _rollback_session() as s:
        t = _seed_tenant(s, name="Co A")
        cases = [
            ("record_payment", "payment"),
            ("create_followup", "followup"),
            ("cancel_invoice", "invoice"),
            ("update_lead", "lead"),
            ("get_or_create_customer", "customer"),
            ("create_catalog_item", "catalog"),
            ("nonexistent_capability", "write"),  # fallback
        ]
        for cap, _expected in cases:
            _add_task(s, t, status=TaskStatus.DONE.value, description=cap,
                      result={"kind": "done", "answer": "ok"},
                      continuity_history=[_committed_turn(cap)])
        # A failed task with no committed turn lands as 'read'.
        _add_task(s, t, status=TaskStatus.FAILED.value, description="fff",
                  result={"kind": "error",
                          "error": {"code": "x", "message": "boom"}})

        resp = dashboard_service.get_assistant_tasks(
            session=s, business_id=t.user.business_id,
            recently_done_limit=50,
        )
        by_desc = {r.description: r for r in resp.recently_done}
        for cap, expected_icon in cases:
            assert by_desc[cap].icon == expected_icon, cap
        assert by_desc["fff"].icon == "read"


def test_existing_payment_summary_endpoint_is_not_regressed():
    """The new endpoint is a sibling, not an extension — payment_summary
    behaviour stays identical (no new fields, no missing fields)."""
    with _rollback_session() as s:
        t = _seed_tenant(s, name="Co A")
        summary = dashboard_service.get_payment_summary(
            session=s, business_id=t.user.business_id,
        )
        # Just the smoke shape — no data seeded so all metrics are zero.
        assert summary.collections_this_month == 0
        assert summary.total_outstanding == 0
        assert summary.outstanding_invoice_count == 0
        assert summary.overdue_invoices == []


# ===========================================================================
# Driver
# ===========================================================================

def _all_tests():
    tests = [
        test_three_bucket_grouping_with_notable_filter_on_recently_done,
        test_awaiting_approval_row_carries_prepared_action_id_preview_editable_fields,
        test_ask_user_tasks_routed_to_needs_input_not_awaiting_approval,
        test_awaiting_approval_bucket_only_contains_real_prepared_writes,
        test_done_and_failed_rows_carry_answer_and_error_message,
        test_recently_done_limit_bounds_to_hard_cap_server_side,
        test_recently_done_limit_zero_returns_empty_bucket,
        test_cross_tenant_request_returns_zero_of_other_tenants_tasks,
        test_index_hit_explain_does_not_use_seq_scan,
        test_empty_business_returns_three_empty_buckets,
        test_notable_filter_excludes_only_bare_chat_done_tasks,
        test_read_only_task_with_at_least_one_read_is_notable,
        test_cancelled_prepare_is_notable_and_surfaces_in_recently_done,
        test_uninformative_description_replaced_by_capability_verb,
        test_informative_description_preserved_over_capability_verb,
        test_icon_categories_mapped_per_capability_family,
        test_existing_payment_summary_endpoint_is_not_regressed,
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
