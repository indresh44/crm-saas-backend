"""Postgres-backed tests for Stage-2 (cross-message) memory.

Covers the contract from the build plan:
  1. A second message in the same session sees the previous batch's
     completed-task results in the LLM prompt (no amnesia).
  2. Three batches deep: only the IMMEDIATELY PRIOR batch is carried —
     message 1's content does NOT appear in message 3's prompt (bloat bound).
  3. First message in a fresh session has no preamble.
  4. Awaiting tasks from the prior batch are NOT carried.
  5. Within-task ask_user resume still works AND the prior-batch preamble
     does NOT stack on top of the ask_user augmentation (precedence rule
     via the shared marker constant).
  6. Confirm-resume does NOT inject the preamble (within-task continuity
     stays untouched).
  7. Failed prior tasks surface as failure context.
  8. Empty/no-read prior task still renders cleanly.

  9. **TENANT GATE (mandatory):** A foreign business cannot see another
     tenant's prior batch via the preamble. This is a hard-required test
     because a leak here injects directly into the LLM context and is
     silent — it cannot be caught downstream.

Run:
    python -m app.agent.tests.test_cross_message_memory_pg
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlmodel import Session

from app.agent.cross_message_memory import (
    NEW_REQUEST_LEAD_IN,
    PREAMBLE_FOOTER,
    PREAMBLE_HEADER,
    build_prior_context_preamble,
)
from app.agent.multi_task_runner import run_message_as_tasks
from app.core.database import engine
from app.core.phone import normalize_phone_value
from app.models.agent_task import AgentTask, TaskStatus
from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import UserRole
from app.models.pipeline import Pipeline, PipelineStage
from app.models.user import User
from app.repositories import agent_chat_repo, agent_task_repo
from app.services import agent_chat_service
from app.services.llm_service import LLMResponse


# ---------------------------------------------------------------------------
# Savepoint-rollback session (shared shape)
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
# Recording fake LLM — captures every (system_prompt, messages) tuple so
# tests can assert on the EXACT prompt the loop sent. The last user message
# in `messages` is the goal text the chat service / runner handed in.
# ---------------------------------------------------------------------------

class _RecordingFakeLLM:
    def __init__(self, *responses):
        self._queue = list(responses)
        self.calls = 0
        self.received: list[tuple[str, list[dict]]] = []

    async def chat(self, system_prompt: str, messages, **kwargs):
        self.calls += 1
        self.received.append((system_prompt, list(messages)))
        assert self._queue, (
            f"FakeLLM ran out of canned responses (call #{self.calls})"
        )
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        content = item if isinstance(item, str) else json.dumps(item)
        return LLMResponse(content=content, tool_calls=None, stop_reason="stop",
                           input_tokens=10, output_tokens=20, model="fake-llm")

    def goals_seen(self) -> list[str]:
        """The user-side goal text for each call (last user message)."""
        out = []
        for _, msgs in self.received:
            users = [m["content"] for m in msgs if m.get("role") == "user"]
            out.append(users[-1] if users else "")
        return out


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@dataclass
class _Fix:
    user: User
    other_user: User              # a separate business + user, for tenant tests
    customer_id: UUID


def _seed(session: Session) -> _Fix:
    bid = uuid4()
    session.add(Business(id=bid, name="Stage2 Test Co", phone="9990000000"))
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
# 1. Cross-message reference resolves via carried context
# ===========================================================================

def test_message2_sees_message1_results_in_prompt():
    """Message 1 reads customers (entity names land in its observation_summary).
    Message 2's loop prompt MUST contain the preamble — the prior task's
    description, status, answer, and the last-read summary with the names."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        llm1 = _RecordingFakeLLM(
            {"thought": "find Rajesh",
             "action": {"type": "read", "query": {
                 "entity": "customers",
                 "filters": [{"field": "name", "op": "contains",
                              "value": "Rajesh"}]}}},
            {"thought": "found",
             "action": {"type": "done", "answer": "Found Rajesh Mehta"}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="find rajesh", llm=llm1,
        ))

        llm2 = _RecordingFakeLLM(
            {"thought": "narrowing by name from prior context",
             "action": {"type": "done",
                        "answer": "Yes, Rajesh is in your customers."}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="is he in my customers?", llm=llm2,
        ))

        # The runner's call to run_agent for message 2 received an augmented
        # goal — preamble + lead-in + original. Verify each delimiter is
        # present and message 1's entity names landed in the prompt.
        goal_seen = llm2.goals_seen()[0]
        assert PREAMBLE_HEADER in goal_seen
        assert PREAMBLE_FOOTER in goal_seen
        assert NEW_REQUEST_LEAD_IN in goal_seen
        assert "find rajesh" in goal_seen           # prior task description
        assert "Found Rajesh Mehta" in goal_seen    # prior answer text
        assert "Rajesh Mehta" in goal_seen          # entity name in last-read
        # And the user's actual new message survives intact at the end.
        assert "is he in my customers?" in goal_seen


# ===========================================================================
# 2. Three batches deep — only the immediately prior batch is carried
# ===========================================================================

def test_message3_carries_only_message2_not_message1():
    """Bloat bound: when message 3 runs, the preamble contains message 2's
    task description but NOT message 1's. A long session does not accumulate
    unbounded context."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        # M1
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="first message about apples",
            llm=_RecordingFakeLLM(
                {"thought": "trivial",
                 "action": {"type": "done", "answer": "apples noted"}},
            ),
        ))
        # M2
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="second message about bananas",
            llm=_RecordingFakeLLM(
                {"thought": "trivial",
                 "action": {"type": "done", "answer": "bananas noted"}},
            ),
        ))
        # M3 — recording LLM so we can inspect the goal
        llm3 = _RecordingFakeLLM(
            {"thought": "trivial",
             "action": {"type": "done", "answer": "cherries noted"}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="third message about cherries",
            llm=llm3,
        ))

        goal_seen = llm3.goals_seen()[0]
        # Message 2 carried.
        assert "second message about bananas" in goal_seen
        assert "bananas noted" in goal_seen
        # Message 1 NOT carried (this is the bloat-bound assertion).
        assert "first message about apples" not in goal_seen
        assert "apples noted" not in goal_seen


# ===========================================================================
# 3. First message in fresh session — no preamble
# ===========================================================================

def test_first_message_in_fresh_session_has_no_preamble():
    """No prior batch -> no preamble. The runner's call sees the bare goal."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        llm = _RecordingFakeLLM(
            {"thought": "trivial",
             "action": {"type": "done", "answer": "hi"}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="hello there friend", llm=llm,
        ))
        goal_seen = llm.goals_seen()[0]
        assert PREAMBLE_HEADER not in goal_seen
        assert PREAMBLE_FOOTER not in goal_seen
        assert NEW_REQUEST_LEAD_IN not in goal_seen
        # Bare goal made it through.
        assert "hello there friend" in goal_seen


# ===========================================================================
# 4. Awaiting tasks from the prior batch are NOT carried
# ===========================================================================

def test_awaiting_prior_task_is_not_carried_in_preamble():
    """Prior batch had one done task + one awaiting_approval task. The
    preamble for the next message contains the done one and NOT the
    awaiting one (no answer yet to reference)."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        # Multi-task batch: task 0 done; task 1 awaiting_confirm.
        llm1 = _RecordingFakeLLM(
            {"tasks": ["list customers",
                       "create new customer Anjali phone 91234 56789"]},
            {"thought": "list",
             "action": {"type": "done",
                        "answer": "1 customer in your database"}},
            {"thought": "prep",
             "action": {"type": "prepare",
                        "capability": "get_or_create_customer",
                        "inputs": {"name": "Anjali Sharma",
                                   "phone": "+91 91234 56789"}}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text=("list customers and create new customer Anjali "
                          "phone 91234 56789 please"),
            llm=llm1,
        ))

        llm2 = _RecordingFakeLLM(
            {"thought": "trivial",
             "action": {"type": "done", "answer": "noted"}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="something completely unrelated to before",
            llm=llm2,
        ))
        goal_seen = llm2.goals_seen()[0]

        # The done task IS in the preamble.
        assert "list customers" in goal_seen
        assert "1 customer in your database" in goal_seen
        # The awaiting task is NOT — its description should not appear in
        # the preamble. (Note: it could appear if it shared text with the
        # user's new message; we chose a description that doesn't.)
        assert "create new customer Anjali" not in goal_seen
        assert "Anjali Sharma" not in goal_seen


# ===========================================================================
# 5. Ask_user augmentation precedence (no double-stack)
# ===========================================================================

def test_ask_user_augmentation_takes_precedence_over_preamble():
    """When the prior message ended in ask_user, the chat service augments
    the goal with the question + reply. The runner detects the augmentation
    marker and does NOT also prepend the prior-batch preamble."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        # M1 ends in ask_user.
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="schedule a follow-up for Rajesh next Tuesday",
            llm=_RecordingFakeLLM(
                {"thought": "ambiguous",
                 "action": {"type": "ask_user",
                            "question": "Which Rajesh — Mehta or Sharma?"}},
            ),
        ))

        # M2 is the disambiguation reply. The chat service injects the
        # augmentation. The runner MUST NOT double-stack a preamble on top.
        llm2 = _RecordingFakeLLM(
            {"thought": "got it",
             "action": {"type": "done", "answer": "ok scheduled"}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="the first one", llm=llm2,
        ))
        goal_seen = llm2.goals_seen()[0]

        # Augmentation marker IS present.
        assert "[Earlier I asked you to clarify:" in goal_seen
        assert "the first one" in goal_seen
        # Preamble markers are NOT present — precedence enforced.
        assert PREAMBLE_HEADER not in goal_seen
        assert PREAMBLE_FOOTER not in goal_seen
        assert NEW_REQUEST_LEAD_IN not in goal_seen


# ===========================================================================
# 6. Confirm-resume does NOT prepend the preamble
# ===========================================================================

def test_confirm_resume_does_not_prepend_preamble():
    """Within-task resume after /confirm uses the task's own continuity
    history. The runner's `_run_one_task` is the only path that prepends
    the preamble; `resume_task_after_commit` does not. Asserts the resumed
    run_agent call sees the bare task description as its goal."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        # Single-task batch -> awaiting_confirm.
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="ensure Rajesh exists",
            llm=_RecordingFakeLLM(
                {"thought": "prep",
                 "action": {"type": "prepare",
                            "capability": "get_or_create_customer",
                            "inputs": {"phone": "+91 99999 00001",
                                       "name": "Rajesh Mehta"}}},
            ),
        ))
        # The session now has one task in awaiting_approval. Confirm it —
        # the resume's LLM call should see no preamble.
        from sqlmodel import select
        task = s.exec(
            select(AgentTask).where(AgentTask.session_id == row.id)
        ).first()
        assert task.status == TaskStatus.AWAITING_APPROVAL.value
        prepared_id = task.pending_action_id

        llm_resume = _RecordingFakeLLM(
            {"thought": "after commit",
             "action": {"type": "done", "answer": "done"}},
        )
        _run(agent_chat_service.handle_confirm(
            s, f.user, session_id=row.id,
            prepared_action_id=prepared_id, edits={}, llm=llm_resume,
        ))

        # The resumed run_agent call had goal = task.description (no preamble).
        assert llm_resume.calls == 1
        resume_goal = llm_resume.goals_seen()[0]
        assert PREAMBLE_HEADER not in resume_goal
        assert "ensure Rajesh exists" in resume_goal


# ===========================================================================
# 7. Failed prior task surfaces as failure context
# ===========================================================================

def test_failed_prior_task_surfaces_in_preamble_as_failed():
    """A task that ended `failed` IS carried — the LLM should know what
    didn't work — but framed as failure context, not as a retry instruction."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        # M1 — loop emits malformed JSON twice -> error.
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="do the broken thing for me please now",
            llm=_RecordingFakeLLM(
                "not json at all",
                "still not json",
            ),
        ))

        # M2 — record the prompt.
        llm2 = _RecordingFakeLLM(
            {"thought": "trivial",
             "action": {"type": "done", "answer": "ok"}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="something else", llm=llm2,
        ))
        goal_seen = llm2.goals_seen()[0]
        # The failed task is carried with a failure status label.
        assert PREAMBLE_HEADER in goal_seen
        assert "do the broken thing for me please now" in goal_seen
        assert "Status: failed" in goal_seen


# ===========================================================================
# 8. Done-with-no-read prior task renders cleanly
# ===========================================================================

def test_done_with_no_read_renders_without_last_read_line():
    """A prior task that went straight to done (no read) renders with
    a `Result:` line and no `Last read:` line. Doesn't crash."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        # M1 — direct done, no reads.
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="say hi",
            llm=_RecordingFakeLLM(
                {"thought": "trivial",
                 "action": {"type": "done", "answer": "hi back"}},
            ),
        ))
        llm2 = _RecordingFakeLLM(
            {"thought": "trivial",
             "action": {"type": "done", "answer": "ok"}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="something else", llm=llm2,
        ))
        goal_seen = llm2.goals_seen()[0]
        assert PREAMBLE_HEADER in goal_seen
        assert "say hi" in goal_seen
        assert "hi back" in goal_seen
        # No read happened in M1, so the card should not carry a `Last read:`
        # line for that task.
        assert "Last read:" not in goal_seen


# ===========================================================================
# 9. **MANDATORY TENANT GATE**: foreign business cannot see another's batch
# ===========================================================================

def test_foreign_business_cannot_see_another_tenants_prior_batch():
    """HARD REQUIREMENT: the helper filters on business_id. A query made
    with a foreign business_id must return None even when chat_session_id
    points at a real session (UUID collision / spoofing scenario). This is
    a leak-into-LLM-context vulnerability if it fails; cannot be caught
    downstream."""
    with _rollback_session() as s:
        f = _seed(s)
        # Tenant A creates a session and a batch with a done task.
        row = agent_chat_service.create_session(s, f.user); s.commit()
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="tenant A secret list of customers please",
            llm=_RecordingFakeLLM(
                {"thought": "trivial",
                 "action": {"type": "done",
                            "answer": "tenant A's secret answer"}},
            ),
        ))
        s.expire_all()

        # Sanity: with TENANT A's business_id, the helper returns a preamble
        # containing the secret answer.
        preamble_owner = build_prior_context_preamble(
            s,
            chat_session_id=row.id,
            business_id=f.user.business_id,
        )
        assert preamble_owner is not None
        assert "tenant A's secret answer" in preamble_owner

        # CRITICAL: with TENANT B's business_id (foreign), the helper MUST
        # return None — no preamble, no leak. Even though the
        # chat_session_id is identical.
        preamble_foreign = build_prior_context_preamble(
            s,
            chat_session_id=row.id,
            business_id=f.other_user.business_id,
        )
        assert preamble_foreign is None, (
            "TENANT BREACH: build_prior_context_preamble returned a non-None "
            "preamble for a foreign business_id. The query is not tenant-"
            "filtered correctly; a leak here injects prior-tenant context "
            "directly into the LLM prompt and is silent."
        )


def test_runner_does_not_carry_other_tenant_batch_into_a_new_session():
    """Defence in depth on top of test 9: when a NEW session is created
    for tenant B and a message is sent, the runner's preamble lookup MUST
    NOT find tenant A's prior batch even though the runner uses session_id
    as one of the filter keys."""
    with _rollback_session() as s:
        f = _seed(s)
        # Tenant A populates a session.
        row_a = agent_chat_service.create_session(s, f.user); s.commit()
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row_a.id,
            message_text="tenant A populates a batch about apples",
            llm=_RecordingFakeLLM(
                {"thought": "trivial",
                 "action": {"type": "done",
                            "answer": "apples answer for tenant A"}},
            ),
        ))

        # Tenant B creates a fresh session and sends a message.
        row_b = agent_chat_service.create_session(s, f.other_user); s.commit()
        llm_b = _RecordingFakeLLM(
            {"thought": "trivial",
             "action": {"type": "done", "answer": "ok"}},
        )
        _run(agent_chat_service.handle_message(
            s, f.other_user, session_id=row_b.id,
            message_text="tenant B's first ever message",
            llm=llm_b,
        ))
        goal_seen = llm_b.goals_seen()[0]
        # No preamble at all (B's session is fresh + tenant scope is hard).
        assert PREAMBLE_HEADER not in goal_seen
        assert "apples answer for tenant A" not in goal_seen
        assert "tenant A" not in goal_seen


# ===========================================================================
# 10. Multi-task new batch — every task in the batch sees the same preamble
# ===========================================================================

def test_every_task_in_a_new_batch_sees_the_same_preamble():
    """The preamble is built once per batch and prepended to every task's
    goal. A two-task new batch should see the same prior-batch context in
    BOTH task prompts."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        # M1
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="prior message about apples",
            llm=_RecordingFakeLLM(
                {"thought": "trivial",
                 "action": {"type": "done", "answer": "apples noted"}},
            ),
        ))

        # M2 — splitter returns two tasks
        llm2 = _RecordingFakeLLM(
            {"tasks": ["check apples again", "also check bananas please"]},
            {"thought": "1",
             "action": {"type": "done", "answer": "apples re-checked"}},
            {"thought": "2",
             "action": {"type": "done", "answer": "bananas noted"}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="check apples again and also check bananas please",
            llm=llm2,
        ))

        # Goals seen: index 0 is the splitter call, indices 1 and 2 are the
        # two task loop calls. Both task calls must contain the preamble.
        goals = llm2.goals_seen()
        assert len(goals) == 3
        for task_goal in goals[1:]:
            assert PREAMBLE_HEADER in task_goal, task_goal
            assert "prior message about apples" in task_goal


# ===========================================================================
# Driver
# ===========================================================================

def _all_tests():
    tests = [
        test_message2_sees_message1_results_in_prompt,
        test_message3_carries_only_message2_not_message1,
        test_first_message_in_fresh_session_has_no_preamble,
        test_awaiting_prior_task_is_not_carried_in_preamble,
        test_ask_user_augmentation_takes_precedence_over_preamble,
        test_confirm_resume_does_not_prepend_preamble,
        test_failed_prior_task_surfaces_in_preamble_as_failed,
        test_done_with_no_read_renders_without_last_read_line,
        test_foreign_business_cannot_see_another_tenants_prior_batch,
        test_runner_does_not_carry_other_tenant_batch_into_a_new_session,
        test_every_task_in_a_new_batch_sees_the_same_preamble,
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
