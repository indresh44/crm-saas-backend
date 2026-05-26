"""Postgres-backed test for agent_chat_service.

Exercises the chat surface end-to-end with a _FakeLLM (same pattern as
test_loop_pg.py): create session, send messages, confirm + cancel, verify
continuity_history is persisted and the COMMITTED/CANCELLED synth records
land on the next call.

Run:
    python -m app.services.tests.test_agent_chat_service_pg
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlmodel import Session, select

from app.agent.serialize import turn_from_dict
from app.core.database import engine
from app.core.phone import normalize_phone_value
from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import UserRole
from app.models.pipeline import Pipeline, PipelineStage
from app.models.user import User
from app.repositories import agent_chat_repo
from app.services import agent_chat_service
from app.services.llm_service import LLMResponse


# ---------------------------------------------------------------------------
# Savepoint-rollback session (same shape as the other write-surface tests)
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
# Fake LLM (canned responses per call)
# ---------------------------------------------------------------------------

class _FakeLLM:
    def __init__(self, *responses):
        self._queue = list(responses)
        self.calls = 0

    async def chat(self, system_prompt: str, messages: list[dict], **kwargs):
        assert self._queue, "FakeLLM ran out of canned responses"
        item = self._queue.pop(0)
        content = item if isinstance(item, str) else json.dumps(item)
        self.calls += 1
        return LLMResponse(content=content, tool_calls=None, stop_reason="stop",
                           input_tokens=10, output_tokens=20, model="fake-llm")


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@dataclass
class _Fix:
    user: User
    stage_id: UUID
    customer_id: UUID


def _seed(session: Session) -> _Fix:
    bid = uuid4()
    session.add(Business(id=bid, name="ChatSvc Test Co", phone="9990000000"))
    session.flush()
    user = User(id=uuid4(), business_id=bid, name="Owner",
                email=f"owner-{bid}@test.local", role=UserRole.OWNER, is_active=True)
    session.add(user); session.flush()
    pid = uuid4()
    session.add(Pipeline(id=pid, business_id=bid, name="Default", is_default=True))
    session.flush()
    sid = uuid4()
    session.add(PipelineStage(id=sid, pipeline_id=pid, name="New", position=1, color="#888"))
    session.flush()
    cid = uuid4()
    session.add(Customer(id=cid, business_id=bid, name="Rajesh Mehta",
                         phone="+91 99999 00001",
                         phone_normalized=normalize_phone_value("+91 99999 00001")))
    session.commit()
    return _Fix(user=user, stage_id=sid, customer_id=cid)


# ===========================================================================
# Tests
# ===========================================================================

def test_create_session_and_round_trip():
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user, title=None)
        s.commit()
        assert row.id and row.business_id == f.user.business_id
        assert row.user_id == f.user.id
        assert row.continuity_history == []
        assert row.awaiting_action_id is None

        row2, msgs, _has_unresolved = agent_chat_service.get_session_with_messages(s, f.user, row.id)
        assert row2.id == row.id and msgs == []


def test_send_message_done_persists_history():
    """A single done outcome persists continuity (empty new turns → empty
    history slice), writes one user + one assistant row, sets title."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        llm = _FakeLLM({"thought": "trivial",
                        "action": {"type": "done", "answer": "hi back"}})
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="say hi", llm=llm,
        ))
        assert env.kind == "done"
        assert env.content == "hi back"
        assert env.awaiting_action_id is None

        s.expire_all()
        row2, msgs, _has_unresolved = agent_chat_service.get_session_with_messages(s, f.user, row.id)
        assert row2.title == "say hi"
        assert [(m.role, m.kind) for m in msgs] == [
            ("user", "text"), ("assistant", "done"),
        ]
        # done appends no new turn -> continuity_history stays empty.
        assert row2.continuity_history == []


def test_continuity_is_per_task_not_per_session_across_messages():
    """REWRITTEN for the multi-task model: continuity is now per-TASK, not
    per-SESSION. A new /message creates a new task with FRESH history — it
    does NOT inherit reads from a prior message's task. (Cross-message
    continuity at the session level is a deliberately-removed v1 behavior.)

    The within-task continuity contract is unchanged and is exercised by the
    new test_multi_task_runner_pg suite via the per-task continuity_history
    assertion."""
    from app.models.agent_task import AgentTask
    from sqlmodel import select

    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        # Message 1 — task does a read and ends DONE. Task's own
        # continuity_history holds the read turn.
        llm1 = _FakeLLM(
            {"thought": "find Rajesh",
             "action": {"type": "read", "query": {
                 "entity": "customers",
                 "filters": [{"field": "name", "op": "contains", "value": "Rajesh"}]}}},
            {"thought": "found",
             "action": {"type": "done", "answer": "Rajesh is here"}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="find rajesh", llm=llm1,
        ))
        s.expire_all()

        # Session-level continuity_history is NO LONGER written. (It still
        # exists on the table for backwards compatibility with pre-0041
        # sessions, but the new flow leaves it alone.)
        row_mid = agent_chat_repo.get_session(
            s, session_id=row.id,
            business_id=f.user.business_id, user_id=f.user.id,
        )
        assert row_mid.continuity_history == []

        # Task-level continuity_history holds the message-1 read turn.
        tasks_for_session = list(s.exec(
            select(AgentTask).where(AgentTask.session_id == row.id)
            .order_by(AgentTask.created_at.asc())
        ).all())
        assert len(tasks_for_session) == 1
        assert len(tasks_for_session[0].continuity_history) == 1
        assert tasks_for_session[0].continuity_history[0]["action"]["type"] == "read"

        # Message 2 creates a NEW task with FRESH continuity — it does NOT see
        # the prior task's reads. If the LLM tried to skip the read it would
        # answer wrong; here we script it to re-read deliberately so we don't
        # blow up the FakeLLM. The assertion is that this is a separate task.
        llm2 = _FakeLLM(
            {"thought": "re-look", "action": {"type": "read", "query": {
                "entity": "customers",
                "filters": [{"field": "name", "op": "contains", "value": "Rajesh"}]}}},
            {"thought": "found again",
             "action": {"type": "done", "answer": "still here"}},
        )
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="any update?", llm=llm2,
        ))
        assert env.kind == "done"

        s.expire_all()
        tasks_after = list(s.exec(
            select(AgentTask).where(AgentTask.session_id == row.id)
            .order_by(AgentTask.created_at.asc())
        ).all())
        # Two distinct tasks (one per message) — they do NOT share continuity.
        assert len(tasks_after) == 2
        assert tasks_after[0].id != tasks_after[1].id
        assert tasks_after[0].batch_id != tasks_after[1].batch_id


def test_awaiting_confirm_then_commit_appends_committed_turn_to_task():
    """REWRITTEN for the multi-task model: the COMMITTED synth turn is now
    appended to the TASK's continuity_history (not the session's), and the
    resumed run_agent runs against that task. The session-level
    awaiting_action_id gate on /message is GONE — a session can have
    multiple awaiting tasks at once. The per-task pending_action_id
    (partial-unique-indexed) is now the gate."""
    from app.repositories import agent_task_repo
    from uuid import UUID

    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        llm = _FakeLLM(
            {"thought": "prepare get-or-create",
             "action": {"type": "prepare",
                        "capability": "get_or_create_customer",
                        "inputs": {"phone": "+91 99999 00001",
                                   "name": "Rajesh Mehta"}}},
        )
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="ensure Rajesh exists", llm=llm,
        ))
        assert env.kind == "awaiting_confirm"
        # Session-level awaiting_action_id is retired — always None now.
        assert env.awaiting_action_id is None
        prepared_id = env.payload["prepared_action_id"]
        task_id = UUID(env.payload["task_id"])

        # Sending another /message while a task is awaiting is now ALLOWED —
        # it spawns a new batch. The session-level 409 gate is gone.
        llm_followup = _FakeLLM(
            {"thought": "trivial",
             "action": {"type": "done", "answer": "noted"}},
        )
        env_followup = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="ok thanks",
            llm=llm_followup,
        ))
        assert env_followup.kind == "done"

        # Confirm the original prepare — resumes the task. Script the resume
        # to immediately return done.
        llm_resume = _FakeLLM(
            {"thought": "post-commit", "action": {"type": "done",
                                                  "answer": "all done"}},
        )
        env2 = _run(agent_chat_service.handle_confirm(
            s, f.user, session_id=row.id,
            prepared_action_id=prepared_id, edits={}, llm=llm_resume,
        ))
        assert env2.kind == "commit_result"
        assert env2.awaiting_action_id is None
        assert str(env2.task_id) == str(task_id)

        # The COMMITTED synth turn now lives on the TASK's continuity_history,
        # not the session's.
        s.expire_all()
        task = agent_task_repo.get_for_update(
            s, task_id=task_id, business_id=f.user.business_id,
        )
        types = [t.get("action", {}).get("type") for t in task.continuity_history]
        # prepare turn from the original loop + COMMITTED synth from confirm.
        # The resume's done action returns early and appends nothing.
        assert "prepare" in types
        assert "committed" in types
        committed = next(
            t for t in task.continuity_history
            if t.get("action", {}).get("type") == "committed"
        )
        assert committed["observation_summary"].startswith("COMMITTED action_id=")
        assert "result" in committed["observation_raw"]

        # Session-level continuity_history is left untouched (deprecated).
        row_after = agent_chat_repo.get_session(
            s, session_id=row.id,
            business_id=f.user.business_id, user_id=f.user.id,
        )
        assert row_after.continuity_history == []


def test_cancel_appends_cancelled_turn_to_task():
    """REWRITTEN for the multi-task model: the CANCELLED synth turn now
    lands on the TASK's continuity_history. The cancel ALSO marks the task
    DONE (cancelled) — the loop is NOT re-invoked because the user said no."""
    from app.repositories import agent_task_repo
    from app.models.agent_task import TaskStatus
    from uuid import UUID

    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        llm = _FakeLLM(
            {"thought": "prepare get-or-create",
             "action": {"type": "prepare",
                        "capability": "get_or_create_customer",
                        "inputs": {"phone": "+91 99999 00001",
                                   "name": "Rajesh Mehta"}}},
        )
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="ensure Rajesh exists", llm=llm,
        ))
        prepared_id = env.payload["prepared_action_id"]
        task_id = UUID(env.payload["task_id"])

        env2 = agent_chat_service.handle_cancel(
            s, f.user, session_id=row.id, prepared_action_id=prepared_id,
        )
        assert env2.kind == "cancelled"
        assert env2.awaiting_action_id is None
        assert str(env2.task_id) == str(task_id)

        s.expire_all()
        task = agent_task_repo.get_for_update(
            s, task_id=task_id, business_id=f.user.business_id,
        )
        assert task.status == TaskStatus.DONE.value
        assert task.pending_action_id is None
        types = [t.get("action", {}).get("type") for t in task.continuity_history]
        assert "prepare" in types and "cancelled" in types
        last_summary = task.continuity_history[-1]["observation_summary"]
        assert last_summary.startswith("CANCELLED action_id=")
        assert "Do NOT re-emit the same prepare" in last_summary


def test_confirm_with_wrong_action_id_is_rejected():
    """A confirm with an action_id that doesn't match any awaiting task in
    the session returns 409. (handle_confirm is now async — the resume
    re-invokes run_agent.)"""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        llm = _FakeLLM(
            {"thought": "prepare get-or-create",
             "action": {"type": "prepare",
                        "capability": "get_or_create_customer",
                        "inputs": {"phone": "+91 99999 00001",
                                   "name": "Rajesh Mehta"}}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="ensure Rajesh exists", llm=llm,
        ))
        from fastapi import HTTPException
        try:
            _run(agent_chat_service.handle_confirm(
                s, f.user, session_id=row.id,
                prepared_action_id="not-the-real-id", edits={},
            ))
            raise AssertionError("expected 409 — id mismatch")
        except HTTPException as exc:
            assert exc.status_code == 409


def test_foreign_session_returns_404():
    """A user from a different business cannot read or post to someone
    else's session."""
    with _rollback_session() as s:
        f1 = _seed(s)
        # second business + user
        bid2 = uuid4()
        s.add(Business(id=bid2, name="Other Co", phone="9991111111")); s.flush()
        other = User(id=uuid4(), business_id=bid2, name="Stranger",
                     email=f"other-{bid2}@test.local",
                     role=UserRole.OWNER, is_active=True)
        s.add(other); s.commit()

        row = agent_chat_service.create_session(s, f1.user); s.commit()

        from fastapi import HTTPException
        try:
            agent_chat_service.get_session_with_messages(s, other, row.id)
            raise AssertionError("expected 404")
        except HTTPException as exc:
            assert exc.status_code == 404


def test_jsonsafe_jsonify_handles_unsafe_types():
    """_safe_jsonify must convert Decimal/UUID/datetime even when buried in
    nested dicts/lists. The boundary REQUIREMENT (risk-1) lives or dies here."""
    from datetime import datetime
    from decimal import Decimal

    from app.agent.serialize import _safe_jsonify
    raw = {
        "amount": Decimal("1234.50"),
        "when": datetime(2026, 5, 23, 12, 0, 0),
        "id": uuid4(),
        "nested": [{"d": Decimal("0.5")}, {"id": uuid4()}],
    }
    safe = _safe_jsonify(raw)
    # Must be json.dumps-able WITHOUT a default= hook.
    json.dumps(safe)


def test_ask_user_reply_augments_goal_with_question_and_original_intent():
    """After an ask_user, the next /message must give the LLM BOTH the
    original goal AND the prior question — not just the bare user reply.
    Without this, the resumed run sees 'first one' with no idea what was
    asked, which is the live bug we observed ('user said 1st one, agent
    re-searched and asked about a totally different lead')."""

    class _RecordingLLM(_FakeLLM):
        """Captures the `messages` arg of each chat() call for assertion."""
        def __init__(self, *responses):
            super().__init__(*responses)
            self.received_messages: list[list[dict]] = []

        async def chat(self, system_prompt, messages, **kwargs):
            self.received_messages.append(messages)
            return await super().chat(system_prompt=system_prompt,
                                      messages=messages, **kwargs)

    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        # Turn 1: read returns rows, then ask_user fires.
        llm1 = _RecordingLLM(
            {"thought": "search Rajesh",
             "action": {"type": "read", "query": {
                 "entity": "customers",
                 "filters": [{"field": "name", "op": "contains", "value": "Rajesh"}]}}},
            {"thought": "ambiguous",
             "action": {"type": "ask_user",
                        "question": "Which Rajesh — Mehta or Sharma?"}},
        )
        original_goal = "Schedule a follow-up for Rajesh next Tuesday"
        env1 = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text=original_goal, llm=llm1,
        ))
        assert env1.kind == "ask_user"

        # Turn 2: user replies. LLM is scripted to emit done — we don't care
        # about the answer, only that the prompt CONTAINS the right context.
        llm2 = _RecordingLLM(
            {"thought": "got it", "action": {"type": "done", "answer": "ok"}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="the first one", llm=llm2,
        ))

        # The LLM's turn-2 prompt must include the original goal AND the
        # ask_user question AND the user's reply. Together these let the LLM
        # disambiguate "the first one" against what was previously asked.
        assert len(llm2.received_messages) == 1
        prompt_text = llm2.received_messages[0][-1]["content"]
        assert original_goal in prompt_text, \
            f"original goal missing from resumed prompt:\n{prompt_text}"
        assert "Which Rajesh — Mehta or Sharma?" in prompt_text, \
            f"ask_user question missing from resumed prompt:\n{prompt_text}"
        assert "the first one" in prompt_text, \
            f"user reply missing from resumed prompt:\n{prompt_text}"


def test_ask_user_followup_resolves_originating_task_out_of_needs_input():
    """Step 2 of the stuck-ask_user fix: when the owner answers an
    ask_user question with a follow-up /message, the ORIGINATING task
    moves from awaiting_approval (needs_input on the dashboard) to a
    terminal DONE state with result.kind='resolved_by_followup' — and
    the new batch's task ids land in its payload for audit."""
    from app.models.agent_task import AgentTask, TaskStatus
    from sqlmodel import select as sqlmodel_select

    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        # Turn 1: ask_user fires → originating task lands in awaiting_approval
        # with pending_action_id=NULL (the needs_input shape).
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="Schedule a follow-up for Rajesh",
            llm=_FakeLLM(
                {"thought": "ambiguous",
                 "action": {"type": "ask_user",
                            "question": "Which Rajesh?"}},
            ),
        ))

        s.expire_all()
        tasks_before = list(s.exec(
            sqlmodel_select(AgentTask).where(AgentTask.session_id == row.id)
        ).all())
        assert len(tasks_before) == 1
        originating_id = tasks_before[0].id
        assert tasks_before[0].status == TaskStatus.AWAITING_APPROVAL.value
        assert tasks_before[0].pending_action_id is None
        assert tasks_before[0].result["kind"] == "ask_user"

        # Turn 2: owner answers. Originating task must transition to DONE +
        # resolved_by_followup; a new batch+task is created for the answer.
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="Rajesh Mehta",
            llm=_FakeLLM(
                {"thought": "got it",
                 "action": {"type": "done", "answer": "Scheduled."}},
            ),
        ))

        s.expire_all()
        originating = s.get(AgentTask, originating_id)
        # The originating task is no longer in awaiting_approval — it's
        # done, with a resolution payload pointing at the new batch.
        assert originating.status == TaskStatus.DONE.value
        assert originating.result["kind"] == "resolved_by_followup"
        assert "resolved_by_batch_id" in originating.result
        assert "resolved_by_task_ids" in originating.result
        assert len(originating.result["resolved_by_task_ids"]) >= 1

        # The new batch's task exists alongside.
        tasks_after = list(s.exec(
            sqlmodel_select(AgentTask).where(AgentTask.session_id == row.id)
            .order_by(AgentTask.created_at.asc())
        ).all())
        assert len(tasks_after) == 2
        new_task = tasks_after[-1]
        assert new_task.id != originating_id
        assert new_task.status == TaskStatus.DONE.value
        assert new_task.result["kind"] == "done"
        # The new task's id is the one in the resolution payload.
        assert str(new_task.id) in originating.result["resolved_by_task_ids"]


def test_ask_user_followup_when_no_originating_task_falls_back_to_old_path():
    """Defence in depth: when the originating-task lookup finds nothing
    (e.g. pre-fix sessions, or a session where the prior batch wasn't
    ask_user), handle_message still works — the new code path is purely
    additive; the fallback to the legacy message-kind augmentation
    keeps working for everything else."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        # Plain done → no ask_user task ever exists.
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="say hi",
            llm=_FakeLLM(
                {"thought": "trivial",
                 "action": {"type": "done", "answer": "hi back"}},
            ),
        ))
        # Follow-up unrelated message — should work normally.
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="something else",
            llm=_FakeLLM(
                {"thought": "trivial",
                 "action": {"type": "done", "answer": "ok"}},
            ),
        ))
        assert env.kind == "done"


def test_ask_user_followup_cross_tenant_isolation():
    """HARD requirement: the originating-task lookup MUST filter by
    business_id. A reply in tenant B's session cannot resolve tenant
    A's ask_user task. Defended at the repo layer with an explicit
    business_id predicate; this test guards it."""
    from app.models.agent_task import AgentTask, TaskStatus

    with _rollback_session() as s:
        # Two tenants, each with their own session.
        f_a = _seed(s)
        bid_b = uuid4()
        s.add(Business(id=bid_b, name="Co B", phone="9991110000")); s.flush()
        user_b = User(
            id=uuid4(), business_id=bid_b, name="B",
            email=f"b-{bid_b}@test.local",
            role=UserRole.OWNER, is_active=True,
        )
        s.add(user_b); s.commit()

        row_a = agent_chat_service.create_session(s, f_a.user); s.commit()
        row_b = agent_chat_service.create_session(s, user_b); s.commit()

        # Tenant A: ask_user pending.
        _run(agent_chat_service.handle_message(
            s, f_a.user, session_id=row_a.id,
            message_text="ask something",
            llm=_FakeLLM(
                {"thought": "?",
                 "action": {"type": "ask_user", "question": "which?"}},
            ),
        ))
        s.expire_all()
        a_task = s.exec(
            select(AgentTask).where(AgentTask.session_id == row_a.id)
        ).first()
        assert a_task.status == TaskStatus.AWAITING_APPROVAL.value

        # Tenant B replies in THEIR OWN session — must NOT touch A's task.
        _run(agent_chat_service.handle_message(
            s, user_b, session_id=row_b.id,
            message_text="some answer",
            llm=_FakeLLM(
                {"thought": "trivial",
                 "action": {"type": "done", "answer": "noted"}},
            ),
        ))
        s.expire_all()
        a_task_after = s.get(AgentTask, a_task.id)
        # A's task UNCHANGED. The new fix does not leak across tenants.
        assert a_task_after.status == TaskStatus.AWAITING_APPROVAL.value
        assert a_task_after.result["kind"] == "ask_user"


def test_awaiting_confirm_compound_carries_answer_text_alongside_preview():
    """The compound case: the user's goal asks BOTH a question and an action.
    The LLM's prepare action carries an optional `answer` field; the chat
    service must surface it as the envelope's `content` while keeping the
    preview in payload. The frontend renders both in one bubble."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()
        llm = _FakeLLM(
            {"thought": "prepare with informational answer",
             "action": {
                 "type": "prepare",
                 "capability": "get_or_create_customer",
                 "inputs": {"phone": "+91 99999 00001", "name": "Rajesh Mehta"},
                 "answer": "Found 1 matching customer (Rajesh Mehta). "
                           "Preparing to ensure the row exists.",
             }},
        )
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="who is Rajesh? ensure his row exists", llm=llm,
        ))
        assert env.kind == "awaiting_confirm"
        # content carries the LLM's answer text (compound preamble)
        assert env.content == ("Found 1 matching customer (Rajesh Mehta). "
                               "Preparing to ensure the row exists.")
        # payload still carries the preview (the confirm card body) + id
        assert env.payload["preview"]
        assert env.payload["prepared_action_id"]
        # Session-level awaiting_action_id is retired — always None now.
        # The prepared_action_id lives in payload (and on the task row).
        assert env.awaiting_action_id is None
        assert env.payload["preview"] != env.content   # distinct concerns


def test_awaiting_confirm_without_answer_has_null_content_back_compat():
    """When the LLM omits `answer` on a prepare (pure-action goal like 'mark
    follow-up X done'), envelope.content is None — the frontend renders just
    the confirm card with no preamble. Back-compat with existing behavior."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()
        llm = _FakeLLM(
            {"thought": "pure action, no answer text needed",
             "action": {
                 "type": "prepare",
                 "capability": "get_or_create_customer",
                 "inputs": {"phone": "+91 99999 00001", "name": "Rajesh Mehta"},
                 # no `answer` field
             }},
        )
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="ensure Rajesh exists", llm=llm,
        ))
        assert env.kind == "awaiting_confirm"
        assert env.content is None
        assert env.payload["preview"]   # preview unchanged


def test_awaiting_confirm_answer_blank_string_treated_as_none():
    """Defensive: an empty / whitespace-only `answer` from the LLM is
    coerced to None so the frontend doesn't render a blank bubble above
    the confirm card."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()
        llm = _FakeLLM(
            {"thought": "blank answer",
             "action": {
                 "type": "prepare",
                 "capability": "get_or_create_customer",
                 "inputs": {"phone": "+91 99999 00001", "name": "Rajesh Mehta"},
                 "answer": "   \n  ",
             }},
        )
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="ensure Rajesh exists", llm=llm,
        ))
        assert env.content is None


def test_done_message_content_unchanged_no_regression():
    """Regression guard: `done` still uses result.answer as the bubble text,
    payload is empty. The compound change only added `answer` to prepare —
    done's contract is the same."""
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
        # Single-task batches now carry task_id + batch_id in the payload so
        # the frontend can target the task on a subsequent /confirm. The
        # done kind otherwise contributes no fields — payload contains
        # exactly these two locator keys.
        assert set(env.payload.keys()) == {"task_id", "batch_id"}
        assert env.payload["task_id"] and env.payload["batch_id"]


def test_ask_user_content_unchanged_no_regression():
    """Regression guard: ask_user still puts the question in content + payload."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()
        llm = _FakeLLM(
            {"thought": "ambiguous",
             "action": {"type": "ask_user",
                        "question": "Which Rajesh?"}},
        )
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="find Rajesh", llm=llm,
        ))
        assert env.kind == "ask_user"
        assert env.content == "Which Rajesh?"
        # ask_user payload now also carries task_id + batch_id (single-task
        # locators) alongside the existing `question` field.
        assert env.payload["question"] == "Which Rajesh?"
        assert env.payload["task_id"] and env.payload["batch_id"]


def test_normal_message_is_not_augmented_when_no_prior_ask_user():
    """Sanity guard: augmentation must NOT fire when the previous assistant
    message was something other than ask_user. A normal follow-up message
    should go through unmodified."""

    class _RecordingLLM(_FakeLLM):
        def __init__(self, *responses):
            super().__init__(*responses)
            self.received_messages: list[list[dict]] = []

        async def chat(self, system_prompt, messages, **kwargs):
            self.received_messages.append(messages)
            return await super().chat(system_prompt=system_prompt,
                                      messages=messages, **kwargs)

    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        llm1 = _FakeLLM(
            {"thought": "trivial", "action": {"type": "done", "answer": "hi"}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="hello", llm=llm1,
        ))

        llm2 = _RecordingLLM(
            {"thought": "ok", "action": {"type": "done", "answer": "ok"}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="thanks", llm=llm2,
        ))
        prompt = llm2.received_messages[0][-1]["content"]
        # Augmentation marker must NOT be present.
        assert "Earlier I asked you to clarify" not in prompt, prompt
        # The bare user message IS present (as the goal text of the new run).
        assert "thanks" in prompt


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run_all() -> int:
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
    raise SystemExit(_run_all())
