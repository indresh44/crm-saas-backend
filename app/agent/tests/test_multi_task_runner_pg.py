"""Postgres-backed tests for the multi-task runner.

Exercises run_message_as_tasks end-to-end with a FAKE LLM (canned action JSONs
per turn) for both the splitter call and each task's loop calls. The runner
shares the savepoint-rollback session pattern used by every other write-side
test in the codebase.

Coverage:
  * Single-task batch — splitter returns one task, loop runs once, task ends
    DONE with the answer.
  * Multi-task batch — splitter returns two tasks, both run independently
    with FRESH continuity each, both end DONE.
  * Failure isolation — one task crashes (exception inside run_agent) and the
    OTHER tasks still run to completion.
  * Awaiting confirm — a task that prepares lands in awaiting_approval with
    pending_action_id set; the unique partial index keeps the lookup
    single-row.

Run:
    python -m app.agent.tests.test_multi_task_runner_pg
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlmodel import Session

from app.agent.multi_task_runner import run_message_as_tasks
from app.core.database import engine
from app.core.phone import normalize_phone_value
from app.models.agent_chat import AgentChatMessage, AgentChatSession
from app.models.agent_task import TaskStatus
from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import UserRole
from app.models.pipeline import Pipeline, PipelineStage
from app.models.user import User
from app.repositories import agent_chat_repo, agent_task_repo
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
# Fake LLM — canned responses; supports both splitter calls (return JSON
# {"tasks": [...]}) and loop calls (return action envelopes).
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
# Fixtures
# ---------------------------------------------------------------------------

@dataclass
class _Fix:
    user: User
    chat_session: AgentChatSession
    user_msg: AgentChatMessage


def _seed(session: Session, *, message_text: str) -> _Fix:
    bid = uuid4()
    session.add(Business(id=bid, name="Runner Test Co", phone="9990000000"))
    session.flush()
    user = User(id=uuid4(), business_id=bid, name="Owner",
                email=f"owner-{bid}@test.local", role=UserRole.OWNER, is_active=True)
    session.add(user); session.flush()
    pid = uuid4()
    session.add(Pipeline(id=pid, business_id=bid, name="Default", is_default=True))
    session.flush()
    session.add(PipelineStage(id=uuid4(), pipeline_id=pid, name="New",
                              position=1, color="#888"))
    session.flush()
    session.add(Customer(id=uuid4(), business_id=bid, name="Rajesh Mehta",
                        phone="+91 99999 00001",
                        phone_normalized=normalize_phone_value("+91 99999 00001")))
    chat_session = agent_chat_repo.create_session(
        session, business_id=bid, user_id=user.id, title=None,
    )
    user_msg = agent_chat_repo.add_message(
        session, session_id=chat_session.id, role="user", kind="text",
        content=message_text,
    )
    session.commit()
    return _Fix(user=user, chat_session=chat_session, user_msg=user_msg)


# ===========================================================================
# Tests
# ===========================================================================

def test_single_task_batch_runs_one_loop_call_to_done():
    """Splitter returns one task (no LLM call; message too short). One
    run_agent call (one fake LLM response). Task ends DONE."""
    with _rollback_session() as s:
        f = _seed(s, message_text="say hi")  # under 30 chars -> splitter skips LLM

        llm = _FakeLLM(
            # loop turn 1: done
            {"thought": "trivial",
             "action": {"type": "done", "answer": "hi back"}},
        )
        batch = _run(run_message_as_tasks(
            s, f.user, f.chat_session, f.user_msg, llm=llm,
        ))
        s.commit()

        assert len(batch.tasks) == 1
        task = batch.tasks[0]
        assert task.status == TaskStatus.DONE.value
        assert task.description == "say hi"
        assert task.sequence_index == 0
        assert task.pending_action_id is None
        assert task.result == {"kind": "done", "answer": "hi back"}
        assert task.batch_id == batch.batch_id
        # Splitter heuristic skipped the LLM; only the loop call counted.
        assert llm.calls == 1


def test_multi_task_batch_runs_each_task_with_fresh_continuity():
    """Splitter returns two tasks. Each task gets its OWN run_agent call with
    EMPTY prior_history — continuity is per-task, not per-batch."""
    with _rollback_session() as s:
        f = _seed(s, message_text=(
            "show me todays followups and check any new enquiries please"
        ))

        llm = _FakeLLM(
            # 1. splitter -> two tasks
            {"tasks": ["show me todays followups",
                       "check any new enquiries please"]},
            # 2. task 0 loop turn 1: done
            {"thought": "easy",
             "action": {"type": "done", "answer": "3 followups today"}},
            # 3. task 1 loop turn 1: done
            {"thought": "easy",
             "action": {"type": "done", "answer": "0 new enquiries"}},
        )
        batch = _run(run_message_as_tasks(
            s, f.user, f.chat_session, f.user_msg, llm=llm,
        ))
        s.commit()

        assert len(batch.tasks) == 2
        t0, t1 = batch.tasks
        assert t0.sequence_index == 0 and t1.sequence_index == 1
        assert t0.description == "show me todays followups"
        assert t1.description == "check any new enquiries please"
        assert t0.status == t1.status == TaskStatus.DONE.value
        assert t0.result["answer"] == "3 followups today"
        assert t1.result["answer"] == "0 new enquiries"
        # batch_id shared.
        assert t0.batch_id == t1.batch_id == batch.batch_id
        # 1 splitter call + 2 loop calls.
        assert llm.calls == 3


def test_failure_in_one_task_does_not_abort_other_tasks():
    """Task 0's loop emits malformed JSON twice (loop returns error). Task 1
    still runs to DONE. The runner's isolation guarantee holds."""
    with _rollback_session() as s:
        f = _seed(s, message_text=(
            "do the broken thing and then list the working things please"
        ))

        llm = _FakeLLM(
            # splitter -> two tasks
            {"tasks": ["do the broken thing",
                       "list the working things please"]},
            # task 0 turn 1: not-json
            "not a json envelope at all",
            # task 0 turn 1 retry: still not-json -> loop returns error
            "still not json",
            # task 1 turn 1: done
            {"thought": "fine",
             "action": {"type": "done", "answer": "two working things"}},
        )
        batch = _run(run_message_as_tasks(
            s, f.user, f.chat_session, f.user_msg, llm=llm,
        ))
        s.commit()

        assert len(batch.tasks) == 2
        t0, t1 = batch.tasks
        assert t0.status == TaskStatus.FAILED.value
        assert t0.result["kind"] == "error"
        # Task 1 succeeded despite task 0's failure.
        assert t1.status == TaskStatus.DONE.value
        assert t1.result["answer"] == "two working things"


def test_unexpected_exception_in_run_agent_marks_task_failed_and_continues():
    """When run_agent itself raises (LLM error), the runner catches it,
    marks the task failed, and moves on to the next task."""
    from app.services.llm_service import LLMError

    with _rollback_session() as s:
        f = _seed(s, message_text=(
            "do the crashy thing and then list the working things please"
        ))

        llm = _FakeLLM(
            {"tasks": ["crashy thing",
                       "list the working things please"]},
            LLMError("simulated upstream failure"),  # task 0 crash
            {"thought": "fine",
             "action": {"type": "done", "answer": "OK"}},  # task 1 success
        )
        batch = _run(run_message_as_tasks(
            s, f.user, f.chat_session, f.user_msg, llm=llm,
        ))
        s.commit()

        t0, t1 = batch.tasks
        assert t0.status == TaskStatus.FAILED.value
        assert t0.result["kind"] == "error"
        assert "simulated upstream failure" in t0.result["error"]["message"]
        assert t1.status == TaskStatus.DONE.value


def test_awaiting_confirm_sets_pending_action_id_and_unique_index_holds():
    """A task that prepares ends in AWAITING_APPROVAL with pending_action_id
    set. The partial UNIQUE index guarantees the /confirm lookup is
    single-row."""
    with _rollback_session() as s:
        f = _seed(s, message_text="create new lead Rajesh phone 99999 99999")

        # The loop prepares get_or_create_customer — a capability that takes
        # only EDITABLE inputs (no LOCKED), so we can stage one without
        # threading any UUIDs through provenance first.
        llm = _FakeLLM(
            # turn 1: prepare get_or_create_customer (locks no fields)
            {"thought": "I'll prepare a get_or_create_customer for Rajesh",
             "action": {"type": "prepare",
                        "capability": "get_or_create_customer",
                        "inputs": {"name": "Rajesh Mehta",
                                   "phone": "+91 99999 99999"}}},
        )
        batch = _run(run_message_as_tasks(
            s, f.user, f.chat_session, f.user_msg, llm=llm,
        ))
        s.commit()

        assert len(batch.tasks) == 1
        task = batch.tasks[0]
        assert task.status == TaskStatus.AWAITING_APPROVAL.value
        assert task.pending_action_id is not None
        assert task.result["kind"] == "awaiting_confirm"
        assert task.result["preview"]

        # Lookup-by-pending-action-id returns the task (single-row).
        found = agent_task_repo.get_by_pending_action_id(
            s,
            prepared_action_id=task.pending_action_id,
            business_id=f.user.business_id,
        )
        assert found is not None and found.id == task.id


def test_per_task_continuity_persisted_across_run_agent_call():
    """A task that does a read then done has TWO entries in its
    continuity_history (read turn + done isn't appended). The history is
    persisted as JSONB on the task row."""
    with _rollback_session() as s:
        f = _seed(s, message_text=(
            "find rajesh and let me know if you find him please now"
        ))

        llm = _FakeLLM(
            # splitter -> single task (LLM gets called because >30 chars + "and")
            {"tasks": ["find rajesh and let me know if you find him please now"]},
            # loop turn 1: read customers
            {"thought": "looking up rajesh",
             "action": {"type": "read", "query": {
                 "entity": "customers",
                 "filters": [{"field": "name", "op": "contains",
                              "value": "Rajesh"}]}}},
            # loop turn 2: done
            {"thought": "found",
             "action": {"type": "done", "answer": "found Rajesh Mehta"}},
        )
        batch = _run(run_message_as_tasks(
            s, f.user, f.chat_session, f.user_msg, llm=llm,
        ))
        s.commit()

        task = batch.tasks[0]
        assert task.status == TaskStatus.DONE.value
        # Read turn is persisted; done returns early without appending.
        assert len(task.continuity_history) == 1
        first_turn = task.continuity_history[0]
        assert first_turn["action"]["type"] == "read"
        assert "observation_summary" in first_turn
        # observation_raw is kept on the persisted row (for UUID provenance).
        assert "observation_raw" in first_turn


def test_clamp_to_4_tasks_remainder_in_4th_slot_preserves_all_text():
    """Splitter returns 5 tasks; the runner clamps to 4 with the 5th merged
    into the 4th slot's description so nothing is silently dropped."""
    with _rollback_session() as s:
        f = _seed(s, message_text=(
            "do A and B and C and D and E please all of them now today"
        ))

        llm = _FakeLLM(
            {"tasks": ["do A", "do B", "do C", "do D", "do E"]},
            # 4 tasks -> 4 loop calls
            {"thought": "1", "action": {"type": "done", "answer": "A done"}},
            {"thought": "2", "action": {"type": "done", "answer": "B done"}},
            {"thought": "3", "action": {"type": "done", "answer": "C done"}},
            {"thought": "4", "action": {"type": "done", "answer": "D and E done"}},
        )
        batch = _run(run_message_as_tasks(
            s, f.user, f.chat_session, f.user_msg, llm=llm,
        ))
        s.commit()

        assert len(batch.tasks) == 4
        # The 4th task's description carries the original 4th + 5th joined.
        last = batch.tasks[-1]
        assert "do D" in last.description and "do E" in last.description
        # All four tasks reached DONE.
        assert all(t.status == TaskStatus.DONE.value for t in batch.tasks)


# ===========================================================================
# Driver
# ===========================================================================

def _all_tests():
    tests = [
        test_single_task_batch_runs_one_loop_call_to_done,
        test_multi_task_batch_runs_each_task_with_fresh_continuity,
        test_failure_in_one_task_does_not_abort_other_tasks,
        test_unexpected_exception_in_run_agent_marks_task_failed_and_continues,
        test_awaiting_confirm_sets_pending_action_id_and_unique_index_holds,
        test_per_task_continuity_persisted_across_run_agent_call,
        test_clamp_to_4_tasks_remainder_in_4th_slot_preserves_all_text,
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
