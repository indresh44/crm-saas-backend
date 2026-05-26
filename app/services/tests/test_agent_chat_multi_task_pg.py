"""Postgres-backed tests for the multi-task chat service.

Exercises the rewired agent_chat_service through /message, /confirm, /cancel
end-to-end with a FAKE LLM. Covers:

  * Single-task /message — assistant message uses an existing MessageKind
    (done / awaiting_confirm) so the frontend renders as before.
  * Multi-task /message — assistant message uses the new `multi_task` kind;
    payload.tasks carries one labeled slot per task in order.
  * Confirm targets a specific task by prepared_action_id; on success the
    task's continuity_history gains a COMMITTED synth turn and the resumed
    run_agent reaches DONE.
  * Cancel marks the task DONE (cancelled) and appends a CANCELLED synth
    turn to the task's continuity_history; the loop is NOT re-invoked.
  * Confirm with a wrong / stale prepared_action_id returns 409.
  * Tenant scoping: a confirm from a different business cannot reach
    another tenant's task.

Run:
    python -m app.services.tests.test_agent_chat_multi_task_pg
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import event
from sqlmodel import Session

from app.core.database import engine
from app.core.phone import normalize_phone_value
from app.models.agent_task import TaskStatus
from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import UserRole
from app.models.pipeline import Pipeline, PipelineStage
from app.models.user import User
from app.repositories import agent_chat_repo, agent_task_repo
from app.services import agent_chat_service
from app.services.llm_service import LLMResponse


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
# Fake LLM
# ---------------------------------------------------------------------------

class _FakeLLM:
    def __init__(self, *responses):
        self._queue = list(responses)
        self.calls = 0

    async def chat(self, system_prompt: str, messages, **kwargs):
        self.calls += 1
        assert self._queue, (
            f"FakeLLM ran out of canned responses (call #{self.calls})"
        )
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        content = item if isinstance(item, str) else json.dumps(item)
        return LLMResponse(content=content, tool_calls=None, stop_reason="stop",
                           input_tokens=10, output_tokens=20, model="fake-llm")


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Seeded fixture — fresh tenant per test
# ---------------------------------------------------------------------------

@dataclass
class _Fix:
    user: User
    other_user: User       # different tenant, used for cross-tenant tests
    customer_id: UUID


def _seed(session: Session) -> _Fix:
    bid = uuid4()
    session.add(Business(id=bid, name="Multi Test Co", phone="9990000000"))
    session.flush()
    user = User(id=uuid4(), business_id=bid, name="Owner",
                email=f"owner-{bid}@test.local",
                role=UserRole.OWNER, is_active=True)
    session.add(user); session.flush()
    pid = uuid4()
    session.add(Pipeline(id=pid, business_id=bid, name="Default", is_default=True))
    session.flush()
    session.add(PipelineStage(id=uuid4(), pipeline_id=pid, name="New",
                              position=1, color="#888"))
    session.flush()
    cid = uuid4()
    session.add(Customer(id=cid, business_id=bid, name="Rajesh Mehta",
                         phone="+91 99999 00001",
                         phone_normalized=normalize_phone_value("+91 99999 00001")))

    # A second tenant for cross-tenant tests.
    other_bid = uuid4()
    session.add(Business(id=other_bid, name="Other Co", phone="9991110000"))
    session.flush()
    other_user = User(id=uuid4(), business_id=other_bid, name="Other Owner",
                      email=f"other-{other_bid}@test.local",
                      role=UserRole.OWNER, is_active=True)
    session.add(other_user); session.flush()
    session.commit()
    return _Fix(user=user, other_user=other_user, customer_id=cid)


# ===========================================================================
# Tests
# ===========================================================================

def test_single_task_message_uses_existing_kind_done():
    """Short message -> splitter skips LLM -> one task -> assistant message
    has kind='done', NOT 'multi_task'."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        llm = _FakeLLM(
            {"thought": "trivial",
             "action": {"type": "done", "answer": "hi back"}},
        )
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="say hi", llm=llm,
        ))
        assert env.kind == "done"
        assert env.content == "hi back"
        # Single-task batches still expose batch_id (uniformity); task_id is
        # None at the envelope top-level (it's inside payload).
        assert env.batch_id is not None
        assert env.task_id is None
        assert env.payload.get("task_id") is not None  # in payload for single-task
        assert env.awaiting_action_id is None


def test_multi_task_message_uses_multi_task_kind_with_labeled_slots():
    """Multi-task batch -> assistant message has kind='multi_task'; each slot
    in payload.tasks is labeled with its description so the user can
    unambiguously confirm the right card."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        llm = _FakeLLM(
            # 1. splitter splits into two
            {"tasks": ["show me todays followups",
                       "check any new enquiries please"]},
            # 2. task 0 done
            {"thought": "1", "action": {"type": "done",
                                        "answer": "3 followups today"}},
            # 3. task 1 done
            {"thought": "2", "action": {"type": "done",
                                        "answer": "0 new enquiries"}},
        )
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="show me todays followups and check any new enquiries please",
            llm=llm,
        ))
        assert env.kind == "multi_task"
        slots = env.payload["tasks"]
        assert len(slots) == 2
        # Labels carry the description (this is the "no wrong-card" guarantee).
        assert slots[0]["description"] == "show me todays followups"
        assert slots[1]["description"] == "check any new enquiries please"
        # Each slot is keyed by task_id + sequence_index for the renderer.
        assert {"task_id", "sequence_index", "kind", "content"} <= set(slots[0].keys())
        assert slots[0]["sequence_index"] == 0 and slots[1]["sequence_index"] == 1
        # Both reached done.
        assert slots[0]["kind"] == "done" and slots[1]["kind"] == "done"
        # Plaintext fallback covers all task descriptions (safety net).
        assert "show me todays followups" in (env.content or "")
        assert "check any new enquiries please" in (env.content or "")


def test_multi_task_fallback_content_carries_full_per_task_answers():
    """Regression guard for the 120-char fallback truncation bug.

    A real run produced two done tasks; the user's frontend hadn't
    implemented the multi_task renderer yet and fell back to rendering
    `content`. The old `_short_outcome_text` capped each task's answer at
    120 chars, so the user saw "...Restaurant interiors (Amit Patel)\\n
    Source: Walk-in\\n    **Es" — mid-word, mid-markdown.

    The fallback now carries the FULL per-task answer text. Per-task
    payloads also live in payload.tasks[].content for proper renderers."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        # A long markdown answer that would have been truncated at 120 chars.
        long_answer = (
            "I found 2 new enquiries from the past week:\n\n"
            "  Restaurant interiors (Amit Patel)\n"
            "    Source: Walk-in\n"
            "    **Estimated Value:** Rs 8,00,000\n"
            "    Stage: New Enquiry\n\n"
            "  Kitchen renovation (Mohit Gupta)\n"
            "    Source: WhatsApp\n"
            "    **Estimated Value:** Rs 1,80,000\n"
            "    Stage: New Enquiry"
        )
        assert len(long_answer) > 120, "test setup must exceed the old cap"

        llm = _FakeLLM(
            {"tasks": ["show me todays followups",
                       "check new enquiries with detailed info"]},
            {"thought": "trivial",
             "action": {"type": "done",
                        "answer": "No follow-ups today, Monday."}},
            {"thought": "found enquiries",
             "action": {"type": "done", "answer": long_answer}},
        )
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text=("show me todays followups and check new "
                          "enquiries with detailed info"),
            llm=llm,
        ))
        assert env.kind == "multi_task"

        # Proper renderer: payload.tasks[1] carries the full answer untouched.
        slots = env.payload["tasks"]
        assert slots[1]["content"] == long_answer

        # Fallback path: every line of the answer survives in `content` too.
        # The boundary character ("**Es" of "**Estimated Value:**") that
        # the 120-char cap used to chop on MUST appear in full.
        assert long_answer in (env.content or ""), (
            "fallback `content` truncated the per-task answer — bug regressed"
        )
        for marker in (
            "**Estimated Value:** Rs 8,00,000",
            "Kitchen renovation",
            "**Estimated Value:** Rs 1,80,000",
        ):
            assert marker in env.content, (
                f"fallback `content` missing {marker!r}; "
                f"a frontend without multi_task support would render "
                f"truncated text. content={env.content!r}"
            )


def test_multi_task_mixed_outcome_stacks_done_and_awaiting_confirm():
    """One task ends DONE; the other prepares -> awaiting_confirm. The
    multi_task slot for the awaiting task carries prepared_action_id +
    preview so the frontend can render a labeled confirm card."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        llm = _FakeLLM(
            {"tasks": ["list customers",
                       "create new customer Anjali phone 91234 56789"]},
            # task 0 -> done
            {"thought": "list",
             "action": {"type": "done", "answer": "1 customer: Rajesh"}},
            # task 1 -> prepare get_or_create_customer
            {"thought": "prep",
             "action": {"type": "prepare",
                        "capability": "get_or_create_customer",
                        "inputs": {"name": "Anjali Sharma",
                                   "phone": "+91 91234 56789"}}},
        )
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text=("list customers and create new customer Anjali "
                          "phone 91234 56789 please"),
            llm=llm,
        ))
        assert env.kind == "multi_task"
        slots = env.payload["tasks"]
        assert slots[0]["kind"] == "done"
        assert slots[1]["kind"] == "awaiting_confirm"
        assert slots[1]["prepared_action_id"]
        assert slots[1]["preview"]
        assert "Anjali" in slots[1]["description"]


def test_confirm_targets_task_by_prepared_action_id_resumes_to_done():
    """Confirm finds the task by prepared_action_id (across all tasks in the
    session), commits, appends synth COMMITTED to THAT TASK's continuity,
    and the resumed run_agent reaches done."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        # Multi-task batch: task 0 done; task 1 awaiting_confirm.
        llm = _FakeLLM(
            {"tasks": ["list customers",
                       "create new customer Anjali phone 91234 56789"]},
            {"thought": "list",
             "action": {"type": "done", "answer": "1 customer"}},
            {"thought": "prep",
             "action": {"type": "prepare",
                        "capability": "get_or_create_customer",
                        "inputs": {"name": "Anjali Sharma",
                                   "phone": "+91 91234 56789"}}},
        )
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text=("list customers and create new customer Anjali "
                          "phone 91234 56789 please"),
            llm=llm,
        ))
        s.expire_all()
        slots = env.payload["tasks"]
        action_id = slots[1]["prepared_action_id"]
        task_id_str = slots[1]["task_id"]

        # Confirm the awaiting task. After commit, resume scripts a done.
        llm2 = _FakeLLM(
            {"thought": "after commit",
             "action": {"type": "done", "answer": "customer created"}},
        )
        env2 = _run(agent_chat_service.handle_confirm(
            s, f.user,
            session_id=row.id,
            prepared_action_id=action_id,
            edits={},
            llm=llm2,
        ))
        assert str(env2.task_id) == task_id_str
        assert env2.batch_id is not None
        # The resumed task ended DONE -> message kind promoted to commit_result.
        assert env2.kind == "commit_result"
        assert "get_or_create_customer" in (env2.content or "")
        assert env2.payload.get("just_committed", {}).get("capability") \
            == "get_or_create_customer"

        # Task row now reflects DONE + cleared pending_action_id; the COMMITTED
        # synth turn is on its continuity_history.
        s.expire_all()
        task = agent_task_repo.get_for_update(
            s, task_id=UUID(task_id_str), business_id=f.user.business_id,
        )
        assert task.status == TaskStatus.DONE.value
        assert task.pending_action_id is None
        assert any(
            t.get("action", {}).get("type") == "committed"
            for t in task.continuity_history
        )


def test_cancel_marks_task_done_and_does_not_invoke_loop():
    """Cancel appends synth CANCELLED to the task's history and marks the
    task done (cancelled). It does NOT re-invoke run_agent (no LLM calls)."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        llm = _FakeLLM(
            # single-task batch -> splitter skipped; loop prepares
            {"thought": "prep",
             "action": {"type": "prepare",
                        "capability": "get_or_create_customer",
                        "inputs": {"name": "Anjali Sharma",
                                   "phone": "+91 91234 56789"}}},
        )
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="create Anjali", llm=llm,
        ))
        s.expire_all()
        # Single-task batch -> kind is awaiting_confirm (existing kind).
        assert env.kind == "awaiting_confirm"
        action_id = env.payload["prepared_action_id"]
        task_id_str = env.payload["task_id"]

        env2 = agent_chat_service.handle_cancel(
            s, f.user,
            session_id=row.id, prepared_action_id=action_id,
        )
        assert env2.kind == "cancelled"
        assert str(env2.task_id) == task_id_str

        s.expire_all()
        task = agent_task_repo.get_for_update(
            s, task_id=UUID(task_id_str), business_id=f.user.business_id,
        )
        # Cancellation is terminal for this task.
        assert task.status == TaskStatus.DONE.value
        assert task.pending_action_id is None
        assert any(
            t.get("action", {}).get("type") == "cancelled"
            for t in task.continuity_history
        )


def test_confirm_with_stale_prepared_action_id_returns_409():
    """Confirming a prepared_action_id that doesn't match any awaiting task
    in this session returns 409 — the gate now lives on the task row."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        try:
            _run(agent_chat_service.handle_confirm(
                s, f.user,
                session_id=row.id,
                prepared_action_id="this-id-does-not-exist",
                edits={},
            ))
        except HTTPException as exc:
            assert exc.status_code == 409
            assert "awaiting" in exc.detail
        else:
            raise AssertionError("expected HTTPException(409)")


def test_cross_tenant_confirm_cannot_reach_other_business_task():
    """A user from tenant B cannot confirm a prepared action that belongs to
    a task in tenant A — get_by_pending_action_id is tenant-scoped, so the
    lookup misses and the handler returns 409."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        llm = _FakeLLM(
            {"thought": "prep",
             "action": {"type": "prepare",
                        "capability": "get_or_create_customer",
                        "inputs": {"name": "Anjali Sharma",
                                   "phone": "+91 91234 56789"}}},
        )
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="create Anjali", llm=llm,
        ))
        action_id = env.payload["prepared_action_id"]

        # Other tenant tries to confirm the same action_id. They don't even
        # have access to the same chat session, so the first check (get
        # session) fails — but if they had their own session they'd still
        # miss on the task lookup. We assert the session-level guard fires.
        other_session = agent_chat_service.create_session(s, f.other_user)
        s.commit()
        try:
            _run(agent_chat_service.handle_confirm(
                s, f.other_user,
                session_id=other_session.id,
                prepared_action_id=action_id,
                edits={},
            ))
        except HTTPException as exc:
            # Could be either 409 (task not in this session) or simply that
            # the task lookup is tenant-scoped and misses. Either way, the
            # cross-tenant access does NOT succeed.
            assert exc.status_code in (404, 409)
        else:
            raise AssertionError(
                "cross-tenant confirm should have raised HTTPException"
            )


# ===========================================================================
# Dismiss endpoint — Step 3 of the stuck-ask_user fix
# ===========================================================================

def test_dismiss_ask_user_task_moves_it_to_done_dismissed():
    """Owner explicitly dismisses a stuck ask_user task. The task moves
    to DONE with result.kind='dismissed' — out of needs_input forever."""
    from app.models.agent_task import AgentTask, TaskStatus
    from sqlmodel import select
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        # Set up: produce an ask_user task.
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="ambiguous request",
            llm=_FakeLLM(
                {"thought": "?",
                 "action": {"type": "ask_user", "question": "Which one?"}},
            ),
        ))
        s.expire_all()
        task = s.exec(
            select(AgentTask).where(AgentTask.session_id == row.id)
        ).first()
        assert task.status == TaskStatus.AWAITING_APPROVAL.value

        # Dismiss.
        env = agent_chat_service.handle_dismiss_task(
            s, f.user, session_id=row.id, task_id=task.id,
        )
        assert env.kind == "cancelled"
        assert "Dismissed" in (env.content or "")
        assert env.payload.get("dismissed_question") == "Which one?"

        s.expire_all()
        task_after = s.get(AgentTask, task.id)
        assert task_after.status == TaskStatus.DONE.value
        assert task_after.result["kind"] == "dismissed"
        assert task_after.pending_action_id is None


def test_dismiss_refuses_real_awaiting_approval_task():
    """Generic endpoint name, narrow body — a real prepared-write task
    cannot be dismissed (must go through /confirm or /cancel). Defends
    against /dismiss becoming a backdoor that skips the CANCELLED
    audit turn."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()
        # Produce a real awaiting_approval via a prepare action.
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="ensure Rajesh",
            llm=_FakeLLM(
                {"thought": "prep",
                 "action": {"type": "prepare",
                            "capability": "get_or_create_customer",
                            "inputs": {"phone": "+91 99999 00001",
                                       "name": "Rajesh Mehta"}}},
            ),
        ))
        s.expire_all()
        from app.models.agent_task import AgentTask
        from sqlmodel import select
        task = s.exec(
            select(AgentTask).where(AgentTask.session_id == row.id)
        ).first()
        assert task.pending_action_id is not None   # real prepared action

        try:
            agent_chat_service.handle_dismiss_task(
                s, f.user, session_id=row.id, task_id=task.id,
            )
            raise AssertionError("expected 409 — dismiss should refuse real awaiting_approval")
        except HTTPException as exc:
            assert exc.status_code == 409
            assert "confirm" in exc.detail or "/confirm" in exc.detail


def test_dismiss_refuses_terminal_task():
    """Dismiss is for needs_input only; already-done tasks can't be
    dismissed (would silently flip their result.kind to dismissed)."""
    from app.models.agent_task import AgentTask, TaskStatus
    from sqlmodel import select
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="hello",
            llm=_FakeLLM(
                {"thought": "trivial",
                 "action": {"type": "done", "answer": "hi"}},
            ),
        ))
        s.expire_all()
        task = s.exec(
            select(AgentTask).where(AgentTask.session_id == row.id)
        ).first()
        assert task.status == TaskStatus.DONE.value

        try:
            agent_chat_service.handle_dismiss_task(
                s, f.user, session_id=row.id, task_id=task.id,
            )
            raise AssertionError("expected 409 — dismiss should refuse done tasks")
        except HTTPException as exc:
            assert exc.status_code == 409


def test_session_detail_marks_resolved_awaiting_confirm_messages():
    """The state-desync fix: an awaiting_confirm message whose underlying
    task has been resolved (confirmed/cancelled/dismissed) must come back
    from GET /sessions/{id} with payload.resolution set so the frontend
    renders disabled buttons instead of live ones."""
    from app.models.agent_task import AgentTask, TaskStatus
    from sqlmodel import select
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()
        # Stage an awaiting_confirm via a prepare.
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="ensure Rajesh",
            llm=_FakeLLM(
                {"thought": "prep",
                 "action": {"type": "prepare",
                            "capability": "get_or_create_customer",
                            "inputs": {"phone": "+91 99999 00001",
                                       "name": "Rajesh Mehta"}}},
            ),
        ))
        s.expire_all()
        task = s.exec(
            select(AgentTask).where(AgentTask.session_id == row.id)
        ).first()
        prepared_action_id = task.pending_action_id

        # Sanity: while task is still awaiting_approval, the message's
        # resolution is None (still live) and has_unresolved_action=True.
        _, msgs_live, has_unresolved_live = (
            agent_chat_service.get_session_with_messages(s, f.user, row.id)
        )
        awaiting_msgs_live = [m for m in msgs_live if m.kind == "awaiting_confirm"]
        assert len(awaiting_msgs_live) == 1
        assert awaiting_msgs_live[0].payload.get("resolution") is None
        assert has_unresolved_live is True

        # Cancel the task (simulates a dashboard cancel or chat /cancel).
        agent_chat_service.handle_cancel(
            s, f.user, session_id=row.id,
            prepared_action_id=prepared_action_id,
        )

        # Now the SAME awaiting_confirm message must come back with
        # resolution='cancelled' — the live confirm/cancel buttons in
        # the chat view should disable.
        _, msgs_resolved, has_unresolved_after = (
            agent_chat_service.get_session_with_messages(s, f.user, row.id)
        )
        awaiting_msgs_after = [m for m in msgs_resolved if m.kind == "awaiting_confirm"]
        assert awaiting_msgs_after[0].payload.get("resolution") == "cancelled"
        assert has_unresolved_after is False


def test_session_detail_marks_confirmed_action_as_resolved_confirmed():
    """A confirmed task should report resolution='confirmed' on the
    awaiting_confirm message that lived in its history."""
    from app.models.agent_task import AgentTask
    from sqlmodel import select
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="ensure Rajesh",
            llm=_FakeLLM(
                {"thought": "prep",
                 "action": {"type": "prepare",
                            "capability": "get_or_create_customer",
                            "inputs": {"phone": "+91 99999 00001",
                                       "name": "Rajesh Mehta"}}},
            ),
        ))
        s.expire_all()
        task = s.exec(
            select(AgentTask).where(AgentTask.session_id == row.id)
        ).first()
        prepared_action_id = task.pending_action_id

        # Confirm.
        _run(agent_chat_service.handle_confirm(
            s, f.user, session_id=row.id,
            prepared_action_id=prepared_action_id, edits={},
            llm=_FakeLLM(
                {"thought": "post-commit",
                 "action": {"type": "done", "answer": "done"}},
            ),
        ))

        _, msgs, has_unresolved = (
            agent_chat_service.get_session_with_messages(s, f.user, row.id)
        )
        awaiting_msgs = [m for m in msgs if m.kind == "awaiting_confirm"]
        assert awaiting_msgs[0].payload.get("resolution") == "confirmed"
        assert has_unresolved is False


def test_session_detail_enriches_multi_task_slots_with_resolution():
    """Multi-task batches carry per-slot prepared_action_id + task_id
    INSIDE payload.tasks[]. The session-detail enrichment must walk
    those slots and add `resolution` per-slot, same as it does for
    single-task awaiting_confirm messages. Without this the chat shows
    a stale 'awaiting your approval' for slots already resolved via
    the dashboard."""
    from app.models.agent_task import AgentTask, TaskStatus
    from sqlmodel import select

    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        # Multi-task batch: two prepares, will land as two awaiting slots.
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text=("create two customers: Anjali phone 91234 00001 "
                          "and Suresh phone 91234 00002"),
            llm=_FakeLLM(
                {"tasks": ["create customer Anjali 91234 00001",
                           "create customer Suresh 91234 00002"]},
                {"thought": "prep A",
                 "action": {"type": "prepare",
                            "capability": "get_or_create_customer",
                            "inputs": {"name": "Anjali",
                                       "phone": "+91 91234 00001"}}},
                {"thought": "prep B",
                 "action": {"type": "prepare",
                            "capability": "get_or_create_customer",
                            "inputs": {"name": "Suresh",
                                       "phone": "+91 91234 00002"}}},
            ),
        ))

        s.expire_all()
        tasks = list(s.exec(
            select(AgentTask).where(AgentTask.session_id == row.id)
            .order_by(AgentTask.sequence_index.asc())
        ).all())
        assert len(tasks) == 2
        task_a, task_b = tasks

        # Before any resolution, both slots in the multi_task message
        # come back with resolution=None (still live).
        _, msgs_live, has_unresolved_live = (
            agent_chat_service.get_session_with_messages(s, f.user, row.id)
        )
        mt_msg = next(m for m in msgs_live if m.kind == "multi_task")
        slots_live = mt_msg.payload["tasks"]
        assert len(slots_live) == 2
        for slot in slots_live:
            assert slot["kind"] == "awaiting_confirm"
            # Per-slot enrichment ran.
            assert "resolution" in slot
            assert slot["resolution"] is None
        # Both slots have a pending real prepared action.
        assert has_unresolved_live is True

        # Resolve task_a via /cancel (simulating dashboard cancel).
        agent_chat_service.handle_cancel(
            s, f.user, session_id=row.id,
            prepared_action_id=task_a.pending_action_id,
        )

        # Now slot[0] must show resolution='cancelled'; slot[1] still
        # None. has_unresolved_action is still True (task_b is pending).
        _, msgs_after, has_unresolved_after = (
            agent_chat_service.get_session_with_messages(s, f.user, row.id)
        )
        mt_msg_after = next(m for m in msgs_after if m.kind == "multi_task")
        slots_after = mt_msg_after.payload["tasks"]
        # Find slots by task_id (sequence_index order may not match the
        # cancel target depending on which task was task_a).
        by_task = {slot["task_id"]: slot for slot in slots_after}
        assert by_task[str(task_a.id)]["resolution"] == "cancelled"
        assert by_task[str(task_b.id)]["resolution"] is None
        assert has_unresolved_after is True

        # Resolve task_b too. Now both slots resolved; gate flips false.
        agent_chat_service.handle_cancel(
            s, f.user, session_id=row.id,
            prepared_action_id=task_b.pending_action_id,
        )
        _, msgs_final, has_unresolved_final = (
            agent_chat_service.get_session_with_messages(s, f.user, row.id)
        )
        mt_final = next(m for m in msgs_final if m.kind == "multi_task")
        for slot in mt_final.payload["tasks"]:
            assert slot["resolution"] == "cancelled"
        assert has_unresolved_final is False


def test_has_unresolved_action_true_only_while_real_prepare_pending():
    """Top-level gate: must be True only when at least one task has
    status=awaiting_approval AND pending_action_id IS NOT NULL. Ask_user
    pauses (pending_action_id NULL) do NOT count — the owner can keep
    chatting; a question doesn't block sending."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()
        # ask_user task — pending_action_id is NULL.
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="ambiguous",
            llm=_FakeLLM(
                {"thought": "?",
                 "action": {"type": "ask_user", "question": "which?"}},
            ),
        ))
        _, _, has_unresolved = (
            agent_chat_service.get_session_with_messages(s, f.user, row.id)
        )
        # Ask_user does NOT gate input — the next /message IS the answer.
        assert has_unresolved is False


def test_dismiss_cross_tenant_returns_404():
    """A user from tenant B cannot dismiss tenant A's task even if they
    somehow guess the task_id. Repo-layer business_id filter is the
    primary defence; session-not-found is the visible result."""
    from app.models.agent_task import AgentTask
    from sqlmodel import select
    with _rollback_session() as s:
        f = _seed(s)
        # Set up: tenant A has an ask_user task.
        row_a = agent_chat_service.create_session(s, f.user); s.commit()
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row_a.id, message_text="ask",
            llm=_FakeLLM(
                {"thought": "?",
                 "action": {"type": "ask_user", "question": "which?"}},
            ),
        ))
        s.expire_all()
        a_task = s.exec(
            select(AgentTask).where(AgentTask.session_id == row_a.id)
        ).first()

        # Tenant B tries to dismiss A's task via B's own session id.
        try:
            agent_chat_service.handle_dismiss_task(
                s, f.other_user,
                session_id=row_a.id,    # A's session
                task_id=a_task.id,
            )
            raise AssertionError("cross-tenant dismiss should fail")
        except HTTPException as exc:
            # 404 — A's session isn't tenant-accessible from B.
            assert exc.status_code == 404


# ===========================================================================
# Driver
# ===========================================================================

def _all_tests():
    tests = [
        test_single_task_message_uses_existing_kind_done,
        test_multi_task_message_uses_multi_task_kind_with_labeled_slots,
        test_multi_task_fallback_content_carries_full_per_task_answers,
        test_multi_task_mixed_outcome_stacks_done_and_awaiting_confirm,
        test_confirm_targets_task_by_prepared_action_id_resumes_to_done,
        test_cancel_marks_task_done_and_does_not_invoke_loop,
        test_confirm_with_stale_prepared_action_id_returns_409,
        test_cross_tenant_confirm_cannot_reach_other_business_task,
        test_dismiss_ask_user_task_moves_it_to_done_dismissed,
        test_dismiss_refuses_real_awaiting_approval_task,
        test_dismiss_refuses_terminal_task,
        test_dismiss_cross_tenant_returns_404,
        test_session_detail_marks_resolved_awaiting_confirm_messages,
        test_session_detail_marks_confirmed_action_as_resolved_confirmed,
        test_session_detail_enriches_multi_task_slots_with_resolution,
        test_has_unresolved_action_true_only_while_real_prepare_pending,
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
