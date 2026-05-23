"""Postgres-backed tests for the agent loop.

Uses a FAKE LLM (scripts canned action JSONs per turn) so the loop's mechanics
are tested without burning real Gemini quota or depending on LLM variance.
Real data: business + customers + leads + pipeline stages, seeded in a savepoint
session so everything rolls back at the end of each test.

Run:
    python -m app.agent.tests.test_loop_pg
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlmodel import Session

from app.agent.confirm import confirm_prepared_action
from app.agent.loop import AgentRunResult, run_agent
from app.agent.observations import READ_ROWS_SUMMARY_CAP, TurnRecord
from app.core.database import engine
from app.core.phone import normalize_phone_value
from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import LeadSource, UserRole
from app.models.lead import Lead
from app.models.pipeline import Pipeline, PipelineStage
from app.models.user import User
from app.services.llm_service import LLMResponse


# ---------------------------------------------------------------------------
# Savepoint-rollback session (same pattern as other write-surface tests)
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
# Fake LLM — returns a canned sequence of envelope dicts (one per turn)
# ---------------------------------------------------------------------------

class _FakeLLM:
    """Each `chat(...)` call returns the next canned response. Responses are
    either an envelope dict (auto-JSON-encoded) or a raw string (for malformed-
    JSON tests)."""

    def __init__(self, *responses):
        self._queue = list(responses)
        self.calls = 0

    async def chat(self, system_prompt: str, messages: list[dict], **kwargs) -> LLMResponse:
        assert self._queue, "FakeLLM ran out of canned responses"
        item = self._queue.pop(0)
        content = item if isinstance(item, str) else json.dumps(item)
        self.calls += 1
        return LLMResponse(
            content=content, tool_calls=None, stop_reason="stop",
            input_tokens=10, output_tokens=20, model="fake-llm",
        )


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@dataclass
class _Fix:
    user: User
    pipeline_id: UUID
    stage_id: UUID
    customer_rajesh_id: UUID


def _seed(session: Session, bid: UUID, *, extra_rajeshes: int = 0) -> _Fix:
    session.add(Business(id=bid, name="AG Test Co", phone="9990000000"))
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
    for i in range(extra_rajeshes):
        session.add(Customer(
            id=uuid4(), business_id=bid, name=f"Rajesh #{i+2}",
            phone=f"+91 99999 1000{i}",
            phone_normalized=normalize_phone_value(f"+91 99999 1000{i}"),
        ))
    session.commit()
    return _Fix(user=user, pipeline_id=pid, stage_id=sid, customer_rajesh_id=cid)


# ===========================================================================
# Core mechanics
# ===========================================================================

def test_loop_done_immediately():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        llm = _FakeLLM({"thought": "trivial", "action": {"type": "done", "answer": "hi"}})
        out: AgentRunResult = _run(run_agent(s, f.user, "say hi", llm=llm))
        assert out.kind == "done" and out.answer == "hi"
        assert out.tokens.turns == 1 and out.tokens.total_input_tokens == 10
        assert llm.calls == 1


def test_loop_read_then_done():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        llm = _FakeLLM(
            {"thought": "look up Rajesh",
             "action": {"type": "read", "query": {
                 "entity": "customers",
                 "filters": [{"field": "name", "op": "contains", "value": "Rajesh"}]}}},
            {"thought": "found", "action": {"type": "done", "answer": "found 1 Rajesh"}},
        )
        out = _run(run_agent(s, f.user, "find Rajesh", llm=llm))
        assert out.kind == "done"
        assert len(out.history) == 1   # only the read produces a TurnRecord
        assert "Rajesh Mehta" in out.history[0].observation_summary


def test_loop_prepare_stops_at_awaiting_confirm_and_makes_no_more_llm_calls():
    """Per spec: structurally unable to reach done after a prepare in the same run."""
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        # Three responses canned; only the first two should be consumed.
        llm = _FakeLLM(
            {"thought": "find customer",
             "action": {"type": "read", "query": {
                 "entity": "customers",
                 "filters": [{"field": "name", "op": "contains", "value": "Rajesh"}]}}},
            {"thought": "prepare get-or-create",
             "action": {"type": "prepare", "capability": "get_or_create_customer",
                        "inputs": {"phone": "+91 99999 00001", "name": "Rajesh Mehta"}}},
            {"thought": "SHOULD NEVER RUN",
             "action": {"type": "done", "answer": "should not get here"}},
        )
        out = _run(run_agent(s, f.user, "ensure Rajesh exists", llm=llm))
        assert out.kind == "awaiting_confirm"
        assert out.prepared_action_id and out.preview
        assert llm.calls == 2, "loop must STOP after prepare — no third LLM call"


def test_loop_hard_stops_on_repeated_action():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        same_read = {"thought": "look again",
                     "action": {"type": "read", "query": {"entity": "customers"}}}
        llm = _FakeLLM(same_read, dict(same_read))
        out = _run(run_agent(s, f.user, "find anything", llm=llm))
        assert out.kind == "error"
        assert out.error and out.error["code"] == "repeated_action"


def test_loop_step_budget_exhausted():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        # 8 distinct reads — the loop hits max_turns without a terminal action.
        responses = [
            {"thought": f"read #{i}", "action": {"type": "read",
                "query": {"entity": "customers", "limit": i + 1}}}
            for i in range(1, 9)
        ]
        out = _run(run_agent(s, f.user, "noop forever", llm=_FakeLLM(*responses)))
        assert out.kind == "exhausted"
        assert out.error and out.error["code"] == "step_budget_exhausted"
        assert out.tokens.turns == 8


def test_loop_bad_action_json_retries_then_errors_cleanly():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        # Two malformed responses for the SAME turn -> error.
        llm = _FakeLLM("not json at all", "{still not a valid envelope}")
        out = _run(run_agent(s, f.user, "x", llm=llm))
        assert out.kind == "error"
        assert out.error and out.error["code"] == "bad_action_json"
        assert llm.calls == 2   # one + one retry


def test_loop_bad_action_json_retry_succeeds():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        llm = _FakeLLM(
            "garbage",
            {"thought": "try again", "action": {"type": "done", "answer": "ok"}},
        )
        out = _run(run_agent(s, f.user, "x", llm=llm))
        assert out.kind == "done" and out.answer == "ok"
        assert llm.calls == 2


# ===========================================================================
# Observation summary: "showing N — narrow filter" when more rows matched
# ===========================================================================

def test_read_more_than_cap_summary_says_so():
    with _rollback_session() as s:
        f = _seed(s, uuid4(), extra_rajeshes=4)   # 1 + 4 = 5 customers
        llm = _FakeLLM(
            {"thought": "list customers",
             "action": {"type": "read", "query": {"entity": "customers"}}},
            {"thought": "stop", "action": {"type": "done", "answer": "done"}},
        )
        out = _run(run_agent(s, f.user, "show customers", llm=llm))
        summary = out.history[0].observation_summary
        # 5 rows matched, summary caps at 3 and says so.
        assert "5 row(s) matched" in summary
        assert f"showing {READ_ROWS_SUMMARY_CAP}" in summary
        assert "narrow the filter" in summary


# ===========================================================================
# UUID provenance — prepare with an unknown UUID is BLOCKED (no layer-2 call)
# ===========================================================================

def test_prepare_with_hallucinated_uuid_is_blocked_locally():
    """A UUID-shaped input that never appeared in a prior read must NOT reach
    the write surface. Loop continues with a 're-read' observation."""
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        hallucinated_lead_id = str(uuid4())   # never appears in any read
        llm = _FakeLLM(
            # Turn 1: prepare directly with a UUID we never read.
            {"thought": "go straight to prepare",
             "action": {"type": "prepare", "capability": "add_lead_note",
                        "inputs": {"lead_id": hallucinated_lead_id,
                                   "description": "auto-generated"}}},
            # Turn 2: ok, re-read leads.
            {"thought": "fine, re-read",
             "action": {"type": "read", "query": {"entity": "leads"}}},
            {"thought": "stop", "action": {"type": "done", "answer": "stopped"}},
        )
        out = _run(run_agent(s, f.user, "add a note somewhere", llm=llm))
        assert out.kind == "done"
        # Turn 1 produced a BLOCKED observation; the write surface never saw the prepare.
        first = out.history[0]
        assert first.observation_raw["kind"] == "uuid_not_in_history"
        assert "BLOCKED" in first.observation_summary
        assert hallucinated_lead_id in first.observation_summary


def test_prepare_with_uuid_seen_in_prior_read_proceeds():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        # Insert a lead so a read returns its id; the LLM then prepares using it.
        from app.models.lead import Lead
        lead_id = uuid4()
        s.add(Lead(id=lead_id, business_id=f.user.business_id, stage_id=f.stage_id,
                   title="Existing", customer_id=f.customer_rajesh_id,
                   source=LeadSource.WHATSAPP))
        s.commit()
        llm = _FakeLLM(
            {"thought": "list leads", "action": {"type": "read", "query": {"entity": "leads"}}},
            {"thought": "add a note",
             "action": {"type": "prepare", "capability": "add_lead_note",
                        "inputs": {"lead_id": str(lead_id),
                                   "description": "follow up next week"}}},
        )
        out = _run(run_agent(s, f.user, "note something", llm=llm))
        assert out.kind == "awaiting_confirm"
        assert out.prepared_action_id


# ===========================================================================
# ask_user terminates the loop without further LLM calls
# ===========================================================================

def test_ask_user_terminates_loop():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        llm = _FakeLLM(
            {"thought": "ambiguous", "action": {"type": "ask_user",
                                                "question": "Which Rajesh do you mean?"}},
            {"thought": "SHOULD NEVER RUN",
             "action": {"type": "done", "answer": "x"}},
        )
        out = _run(run_agent(s, f.user, "find Rajesh", llm=llm))
        assert out.kind == "ask_user"
        assert "Which Rajesh" in (out.question or "")
        assert llm.calls == 1


# ===========================================================================
# Confirm path (separate function, NOT in the loop)
# ===========================================================================

def test_confirm_after_prepare_commits_and_returns_result():
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        llm = _FakeLLM(
            {"thought": "use existing rajesh by phone",
             "action": {"type": "prepare", "capability": "get_or_create_customer",
                        "inputs": {"phone": "+91 99999 00001", "name": "Rajesh Mehta"}}},
        )
        out = _run(run_agent(s, f.user, "ensure Rajesh exists", llm=llm))
        assert out.kind == "awaiting_confirm"

        cr = confirm_prepared_action(s, f.user, action_id=out.prepared_action_id)
        assert cr.ok and cr.result is not None
        # Existing customer -> created=False, returns the same customer_id.
        assert cr.result["created"] is False
        assert cr.result["customer_id"] == str(f.customer_rajesh_id)


# ===========================================================================
# End-to-end demo flavour: read customer -> prepare create_lead with that id
# (full chained demo is run-and-rerun by design; this exercises one round)
# ===========================================================================

def test_demo_round_one_resolves_existing_customer_and_prepares_create_lead():
    """User goal: 'create a 3BHK lead for Rajesh'. Loop: read customers, read
    pipeline-stage-bearing leads to discover the stage_id, then prepare
    create_lead. The fixture has an existing Rajesh + stage so this finishes in
    three turns (read customers, read leads-or-similar to source stage_id, prepare)."""
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        # Seed one lead in the existing stage so a read on leads exposes stage_id.
        from app.models.lead import Lead
        existing = uuid4()
        s.add(Lead(id=existing, business_id=f.user.business_id, stage_id=f.stage_id,
                   title="Seed", customer_id=f.customer_rajesh_id, source=LeadSource.WHATSAPP))
        s.commit()

        llm = _FakeLLM(
            # Turn 1: find Rajesh -> customer_id appears in observation.
            {"thought": "resolve Rajesh",
             "action": {"type": "read", "query": {
                 "entity": "customers",
                 "filters": [{"field": "name", "op": "contains", "value": "Rajesh"}]}}},
            # Turn 2: read leads to source a stage_id (since pipeline_stages isn't queryable yet).
            {"thought": "need a stage_id; read leads",
             "action": {"type": "read", "query": {"entity": "leads", "limit": 1}}},
            # Turn 3: prepare create_lead with the IDs from turns 1 + 2.
            {"thought": "prepare the lead",
             "action": {"type": "prepare", "capability": "create_lead",
                        "inputs": {
                            "stage_id": str(f.stage_id),
                            "customer_id": str(f.customer_rajesh_id),
                            "title": "3BHK interiors",
                        }}},
        )
        out = _run(run_agent(s, f.user, "create a 3BHK lead for Rajesh", llm=llm))
        assert out.kind == "awaiting_confirm", out.to_dict()
        assert "3BHK interiors" in (out.preview or "")
        assert out.tokens.turns == 3


# ===========================================================================
# Stage 1 memory — prior_history makes the loop continuation-aware
# ===========================================================================
#
# These tests verify the LOOP MECHANICS (history is threaded, turns continue,
# raw stays available for provenance, no regression without prior_history).
# They use _FakeLLM scripts whose canned responses ASSUME the LLM correctly
# applies the CONTINUATION rule. They do NOT — and structurally cannot —
# verify that real Gemini will actually apply that rule when it sees a
# COMMITTED turn. That can only be confirmed in a live Gemini run; the
# scripted test confirms only that IF the LLM behaves correctly, the loop
# carries it through correctly.

def test_no_prior_history_unchanged_behavior():
    """Regression guard: omitting prior_history must behave exactly as before."""
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        llm = _FakeLLM(
            {"thought": "look up Rajesh",
             "action": {"type": "read", "query": {
                 "entity": "customers",
                 "filters": [{"field": "name", "op": "contains", "value": "Rajesh"}]}}},
            {"thought": "found", "action": {"type": "done", "answer": "found 1 Rajesh"}},
        )
        out = _run(run_agent(s, f.user, "find Rajesh", llm=llm))
        assert out.kind == "done" and out.answer == "found 1 Rajesh"
        assert llm.calls == 2
        assert len(out.history) == 1   # only the read appended; identical to prior behavior
        assert out.history[0].turn == 1


def test_prior_history_prevents_repeated_read():
    """With a prior read already in history, an LLM scripted to trust it
    emits done on its FIRST turn without reissuing the read."""
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        # Prior turn: a completed read on customers naming Rajesh, with raw
        # rows carrying the customer_id so UUID provenance is preserved.
        rajesh_row = {"id": str(f.customer_rajesh_id), "name": "Rajesh Mehta",
                      "phone": "+91 99999 00001"}
        prior_read = TurnRecord(
            turn=1,
            thought="(prior) look up Rajesh",
            action={"type": "read", "query": {
                "entity": "customers",
                "filters": [{"field": "name", "op": "contains", "value": "Rajesh"}]}},
            observation_summary=("read on customers: 1 row(s) matched.\n"
                                 f"  row 1: id={f.customer_rajesh_id}, name=Rajesh Mehta"),
            observation_raw={"kind": "rows", "entity": "customers",
                             "count": 1, "rows": [rajesh_row]},
        )
        llm = _FakeLLM(
            {"thought": "prior turn already answered this",
             "action": {"type": "done",
                        "answer": f"Rajesh's id is {f.customer_rajesh_id}"}},
        )
        out = _run(run_agent(
            s, f.user, "what is Rajesh's customer id?",
            llm=llm, prior_history=(prior_read,),
        ))
        assert out.kind == "done"
        assert llm.calls == 1, "LLM made more than one call — it re-read the customer"
        # History is unchanged (done appends nothing) — exactly the prior tuple.
        assert len(out.history) == 1
        assert out.history[0] is prior_read
        # Turn numbering continued from prior_count + 1 internally even though
        # no new turn was appended — verified by token report turn count.
        assert out.tokens.turns == 1


def test_resumed_after_committed_write_emits_done_without_new_prepare():
    """Prior history: read -> prepare -> synthesized COMMITTED. The LLM,
    scripted to obey the CONTINUATION rule, emits done on turn 1 and never
    re-prepares — which is the structural fix for the duplicate-write bug."""
    with _rollback_session() as s:
        f = _seed(s, uuid4())
        # Build a realistic prior chain for "add a note to a lead".
        from app.models.lead import Lead
        lead_id = uuid4()
        s.add(Lead(id=lead_id, business_id=f.user.business_id, stage_id=f.stage_id,
                   title="Existing", customer_id=f.customer_rajesh_id,
                   source=LeadSource.WHATSAPP))
        s.commit()

        lead_row = {"id": str(lead_id), "title": "Existing",
                    "customer_id": str(f.customer_rajesh_id)}
        prior_read = TurnRecord(
            turn=1, thought="(prior) find the lead",
            action={"type": "read", "query": {"entity": "leads"}},
            observation_summary=("read on leads: 1 row(s) matched.\n"
                                 f"  row 1: id={lead_id}, title=Existing"),
            observation_raw={"kind": "rows", "entity": "leads",
                             "count": 1, "rows": [lead_row]},
        )
        action_id = str(uuid4())
        prior_prepare = TurnRecord(
            turn=2, thought="(prior) prepare the note",
            action={"type": "prepare", "capability": "add_lead_note",
                    "inputs": {"lead_id": str(lead_id),
                               "description": "follow up next week"}},
            observation_summary=(f"PREPARED action_id={action_id} — awaiting human confirm"),
            observation_raw={"kind": "prepared", "action_id": action_id,
                             "capability": "add_lead_note"},
        )
        committed = TurnRecord(
            turn=3, thought="(harness) commit landed",
            action={"type": "committed", "action_id": action_id,
                    "capability": "add_lead_note"},
            observation_summary=(f"COMMITTED action_id={action_id} "
                                 f"capability='add_lead_note' — the write LANDED "
                                 f"in the database. Do NOT prepare this again."),
            observation_raw={"kind": "committed", "action_id": action_id,
                             "capability": "add_lead_note",
                             "result": {"note_id": str(uuid4())}},
        )
        prior = (prior_read, prior_prepare, committed)

        # Script the LLM to obey the rule: see COMMITTED, emit done. Add a
        # second canned response that MUST NOT be consumed (a redundant
        # prepare) — if the loop somehow took it, llm.calls would be > 1 and
        # the assertion below catches it.
        llm = _FakeLLM(
            {"thought": "note already committed — nothing to do",
             "action": {"type": "done",
                        "answer": "note already added in the previous step"}},
            {"thought": "SHOULD NEVER RUN — would duplicate the note",
             "action": {"type": "prepare", "capability": "add_lead_note",
                        "inputs": {"lead_id": str(lead_id),
                                   "description": "follow up next week"}}},
        )
        out = _run(run_agent(
            s, f.user, "add the follow-up note to that lead",
            llm=llm, prior_history=prior,
        ))
        assert out.kind == "done"
        assert llm.calls == 1, "LLM was called more than once — risk of re-prepare"
        # No new turns were appended (done is terminal-without-record).
        assert len(out.history) == 3
        # And belt-and-braces: no prepare exists in history other than the prior one.
        prepare_count = sum(1 for t in out.history if t.action.get("type") == "prepare")
        assert prepare_count == 1, "a second prepare was appended — duplicate-write bug"


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
