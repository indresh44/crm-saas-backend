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
from sqlmodel import Session

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

        row2, msgs = agent_chat_service.get_session_with_messages(s, f.user, row.id)
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
        row2, msgs = agent_chat_service.get_session_with_messages(s, f.user, row.id)
        assert row2.title == "say hi"
        assert [(m.role, m.kind) for m in msgs] == [
            ("user", "text"), ("assistant", "done"),
        ]
        # done appends no new turn -> continuity_history stays empty.
        assert row2.continuity_history == []


def test_continuity_carries_across_messages():
    """Second message sees the first message's read turn as prior_history;
    scripted LLM uses it without re-reading."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        llm1 = _FakeLLM(
            {"thought": "find Rajesh",
             "action": {"type": "read", "query": {
                 "entity": "customers",
                 "filters": [{"field": "name", "op": "contains", "value": "Rajesh"}]}}},
            {"thought": "found", "action": {"type": "done", "answer": "Rajesh is here"}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="find rajesh", llm=llm1,
        ))
        s.expire_all()
        row_mid = agent_chat_repo.get_session(
            s, session_id=row.id, business_id=f.user.business_id, user_id=f.user.id,
        )
        # The read turn is persisted; the done is not (done appends nothing).
        assert len(row_mid.continuity_history) == 1
        assert row_mid.continuity_history[0]["action"]["type"] == "read"

        # Second message: LLM is given ONE response (done) — if it tried to
        # read again it would run out of canned responses and the assertion in
        # _FakeLLM would fire. Continuity is what lets it answer directly.
        llm2 = _FakeLLM({"thought": "already known from prior turn",
                         "action": {"type": "done",
                                    "answer": f"customer_id is {f.customer_id}"}})
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="what's the id?", llm=llm2,
        ))
        assert env.kind == "done"
        assert llm2.calls == 1


def test_awaiting_confirm_then_commit_appends_committed_turn():
    """Prepare -> awaiting_action_id set; confirm -> committed turn appended
    to continuity_history; subsequent send sees it as prior_history."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        # Prepare via get_or_create_customer (matches existing chosen-by-tests
        # capability that doesn't need a stage_id flow).
        llm = _FakeLLM(
            {"thought": "prepare get-or-create",
             "action": {"type": "prepare", "capability": "get_or_create_customer",
                        "inputs": {"phone": "+91 99999 00001",
                                   "name": "Rajesh Mehta"}}},
        )
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id,
            message_text="ensure Rajesh exists", llm=llm,
        ))
        assert env.kind == "awaiting_confirm"
        assert env.awaiting_action_id == env.payload["prepared_action_id"]
        prepared_id = env.payload["prepared_action_id"]

        # Sending another message while awaiting -> 409
        from fastapi import HTTPException
        try:
            _run(agent_chat_service.handle_message(
                s, f.user, session_id=row.id, message_text="anything",
                llm=_FakeLLM(),
            ))
            raise AssertionError("expected 409 — session is awaiting confirm")
        except HTTPException as exc:
            assert exc.status_code == 409

        # Confirm — should commit + append the COMMITTED synth turn.
        env2 = agent_chat_service.handle_confirm(
            s, f.user, session_id=row.id,
            prepared_action_id=prepared_id, edits={},
        )
        assert env2.kind == "commit_result"
        assert env2.awaiting_action_id is None
        s.expire_all()
        row_after = agent_chat_repo.get_session(
            s, session_id=row.id, business_id=f.user.business_id, user_id=f.user.id,
        )
        types = [t.action.get("type")
                 for t in (turn_from_dict(d) for d in row_after.continuity_history)]
        assert types == ["prepare", "committed"]
        committed = row_after.continuity_history[-1]
        assert committed["observation_summary"].startswith("COMMITTED action_id=")
        assert "result" in committed["observation_raw"]


def test_cancel_appends_cancelled_turn():
    """Cancel must record a CANCELLED synthetic turn so the LLM sees the
    rejection — not just leave the bare prepare in history."""
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        llm = _FakeLLM(
            {"thought": "prepare get-or-create",
             "action": {"type": "prepare", "capability": "get_or_create_customer",
                        "inputs": {"phone": "+91 99999 00001",
                                   "name": "Rajesh Mehta"}}},
        )
        env = _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="ensure Rajesh exists", llm=llm,
        ))
        prepared_id = env.payload["prepared_action_id"]

        env2 = agent_chat_service.handle_cancel(
            s, f.user, session_id=row.id, prepared_action_id=prepared_id,
        )
        assert env2.kind == "cancelled"
        assert env2.awaiting_action_id is None
        s.expire_all()
        row_after = agent_chat_repo.get_session(
            s, session_id=row.id, business_id=f.user.business_id, user_id=f.user.id,
        )
        types = [t["action"].get("type") for t in row_after.continuity_history]
        assert types == ["prepare", "cancelled"]
        last_summary = row_after.continuity_history[-1]["observation_summary"]
        assert last_summary.startswith("CANCELLED action_id=")
        assert "Do NOT re-emit the same prepare" in last_summary


def test_confirm_with_wrong_action_id_is_rejected():
    with _rollback_session() as s:
        f = _seed(s)
        row = agent_chat_service.create_session(s, f.user); s.commit()

        llm = _FakeLLM(
            {"thought": "prepare get-or-create",
             "action": {"type": "prepare", "capability": "get_or_create_customer",
                        "inputs": {"phone": "+91 99999 00001",
                                   "name": "Rajesh Mehta"}}},
        )
        _run(agent_chat_service.handle_message(
            s, f.user, session_id=row.id, message_text="ensure Rajesh exists", llm=llm,
        ))
        from fastapi import HTTPException
        try:
            agent_chat_service.handle_confirm(
                s, f.user, session_id=row.id,
                prepared_action_id="not-the-real-id", edits={},
            )
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
