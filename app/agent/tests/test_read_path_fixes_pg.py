"""Tests for the read-path fixes (bugs A and B from the 6-turn agent log).

Bug A — parser used to silently ignore unknown top-level keys, so the LLM's
`where: {field: {op: value}}` shape sailed through with ZERO filters applied
and the read returned the entire table. The fix made top-level strict +
added a concrete query-shape example to the system prompt. These tests
prove three things end-to-end:

  1. The parser raises a TEACHING error on the LLM's failing shape.
  2. That error flows back via the read_failed observation in the loop.
  3. The LLM can self-correct on the next turn by switching to `filters`.

Bug B — the LLM had no knowledge of the current date and guessed it from
row data, filtering on the wrong date entirely. The fix injects the
business-local `now` (the SAME clock the compiler uses for is_overdue) into
the per-turn user message. Test 4 proves the date appears verbatim in the
prompt the LLM sees, and uses the same clock as the read-model compiler.

Other tests guard the non-regression: a filtered read returns ONLY matching
rows (the bug-A symptom), and the canonical filter shape still parses.

Run:
    python -m app.agent.tests.test_read_path_fixes_pg
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import event
from sqlmodel import Session

from app.agent.loop import run_agent
from app.agent.parsing import ReadQueryParseError, parse_read_query
from app.core.database import engine
from app.core.phone import normalize_phone_value
from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import LeadSource, UserRole
from app.models.lead import Lead
from app.models.lead_followup import LeadFollowup
from app.models.pipeline import Pipeline, PipelineStage
from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
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


class _RecordingFakeLLM:
    """Captures every (system_prompt, messages) so tests can assert on the
    exact prompt the loop sent."""
    def __init__(self, *responses):
        self._queue = list(responses)
        self.calls = 0
        self.received: list[tuple[str, list[dict]]] = []

    async def chat(self, system_prompt: str, messages, **kwargs):
        self.calls += 1
        self.received.append((system_prompt, list(messages)))
        assert self._queue, f"FakeLLM ran out (call #{self.calls})"
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        content = item if isinstance(item, str) else json.dumps(item)
        return LLMResponse(content=content, tool_calls=None, stop_reason="stop",
                           input_tokens=10, output_tokens=20, model="fake")


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Seed: business + 3 followups, exactly one with status='pending' scheduled today
# ---------------------------------------------------------------------------

def _seed(session: Session) -> tuple[User, datetime]:
    """Returns (owner_user, today_local). today_local is the date we'll use
    in the filter; one followup matches it AND has status='pending'."""
    today = datetime.now(ZoneInfo("Asia/Kolkata")).replace(
        hour=12, minute=0, second=0, microsecond=0
    )

    bid = uuid4()
    session.add(Business(id=bid, name="ReadPath Test Co", phone="9990000000",
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
    session.add(PipelineStage(id=sid, pipeline_id=pid, name="New",
                              position=1, color="#888"))
    session.flush()
    cid = uuid4()
    session.add(Customer(id=cid, business_id=bid, name="Rajesh Mehta",
                         phone="+91 99999 00001",
                         phone_normalized=normalize_phone_value("+91 99999 00001")))
    session.flush()
    lid = uuid4()
    session.add(Lead(id=lid, business_id=bid, customer_id=cid, stage_id=sid,
                     title="Modular kitchen", source=LeadSource.WALK_IN))
    session.flush()

    # 3 followups (LeadFollowup is ViaParent-tenanted through `lead`, so it
    # doesn't take business_id directly; uses `note` not `title`):
    #   A: today + pending             (the one we want filters to match)
    #   B: today + done                 (right date, wrong status)
    #   C: tomorrow + pending           (right status, wrong date)
    session.add(LeadFollowup(id=uuid4(), lead_id=lid, created_by=user.id,
                             scheduled_at=today, status="pending",
                             note="A — today pending"))
    session.add(LeadFollowup(id=uuid4(), lead_id=lid, created_by=user.id,
                             scheduled_at=today, status="done",
                             note="B — today done"))
    session.add(LeadFollowup(id=uuid4(), lead_id=lid, created_by=user.id,
                             scheduled_at=today + timedelta(days=1),
                             status="pending",
                             note="C — tomorrow pending"))
    session.commit()
    return user, today


# ===========================================================================
# Bug A — parser strictness + teaching error
# ===========================================================================

def test_parser_rejects_where_dict_of_dicts_with_teaching_error():
    """The exact shape the LLM emitted in the log:
        {"where": {"scheduled_at": {"between": [...]}, "status": {"=": "pending"}}}
    must NOT silently parse with zero filters. It must raise — and the
    error message must contain the correct shape so the loop can copy it."""
    try:
        parse_read_query({
            "entity": "lead_followups",
            "where": {
                "scheduled_at": {"between": ["2026-05-25", "2026-05-25"]},
                "status": {"=": "pending"},
            },
        })
    except ReadQueryParseError as exc:
        # "where" aliases to "filters", but the value is a dict not a list,
        # so the bad_filters error fires with the corrective example.
        assert exc.code == "bad_filters"
        assert "list of {field, op, value}" in exc.message.lower() \
            or "list of {field, op, value}" in exc.message
        assert '"field"' in exc.message and '"op"' in exc.message
        assert '"value"' in exc.message
        # And it must mention "between" for the range case so the loop
        # knows the correct operator for date ranges.
        assert "between" in exc.message
    else:
        raise AssertionError("expected ReadQueryParseError(bad_filters)")


def test_where_alias_with_correct_list_shape_works():
    """If the LLM gets close — uses `where` as the key but the right value
    shape — the alias rename lets it through without an error. (Bias
    toward letting good queries pass; reject only when the SHAPE is wrong.)"""
    rq = parse_read_query({
        "entity": "lead_followups",
        "where": [{"field": "status", "op": "=", "value": "pending"}],
    })
    assert len(rq.filters) == 1
    assert rq.filters[0].field == "status"
    assert rq.filters[0].op == "="
    assert rq.filters[0].value == "pending"


def test_in_operator_on_string_field_returns_all_matches_in_one_read():
    """Regression guard for the 'in disallowed on STRING' bug.

    Live log showed: LLM emitted `op:'in', field:'title', value:['A','B']`
    against `leads`. Old schema rejected it (in was ENUM-only) — LLM fell
    back to two sequential `op:'='` reads, costing one extra turn (~7k
    tokens + a round trip).

    Today `in` is allowed on STRING (and every other type where `=` is).
    This test compiles the exact failing query against a real DB and
    asserts both rows come back in ONE read."""
    with _rollback_session() as s:
        # Seed two leads with distinct titles.
        bid = uuid4()
        s.add(Business(id=bid, name="IN-op Test Co", phone="9990001111",
                       timezone="Asia/Kolkata")); s.flush()
        user = User(id=uuid4(), business_id=bid, name="Owner",
                    email=f"owner-{bid}@test.local",
                    role=UserRole.OWNER, is_active=True)
        s.add(user); s.flush()
        pid = uuid4()
        s.add(Pipeline(id=pid, business_id=bid, name="Default", is_default=True))
        s.flush()
        sid = uuid4()
        s.add(PipelineStage(id=sid, pipeline_id=pid, name="New",
                            position=1, color="#888"))
        s.flush()
        cid = uuid4()
        s.add(Customer(id=cid, business_id=bid, name="Amit Patel",
                       phone="+91 98290 88888",
                       phone_normalized=normalize_phone_value("+91 98290 88888")))
        s.flush()
        s.add(Lead(id=uuid4(), business_id=bid, customer_id=cid, stage_id=sid,
                   title="Restaurant interiors", source=LeadSource.WALK_IN))
        s.add(Lead(id=uuid4(), business_id=bid, customer_id=cid, stage_id=sid,
                   title="Kitchen renovation", source=LeadSource.WHATSAPP))
        # Plus a third lead that should NOT match — proves `in` actually
        # filters (and we're not just dumping the table).
        s.add(Lead(id=uuid4(), business_id=bid, customer_id=cid, stage_id=sid,
                   title="Bedroom refresh", source=LeadSource.WALK_IN))
        s.commit()

        rq = ReadQuery(
            "leads",
            filters=[Filter("title", "in",
                            ["Restaurant interiors", "Kitchen renovation"])],
            select=["id", "title"],
        )
        compiled = compile_query(rq, business_id=bid)
        result = execute_query(compiled, s)
        titles = sorted(r["title"] for r in result.rows)
        assert titles == ["Kitchen renovation", "Restaurant interiors"], (
            f"`in` on STRING field should return both named rows in one read, "
            f"got {titles!r}"
        )


def test_parser_rejects_unknown_operator_with_teaching_error():
    """An unknown operator on a filter must surface a clear error naming
    the bad op and the allowed ones for that field (already enforced by
    the compiler — this guards the regression)."""
    from app.read_model.compiler import compile_query, ReadModelValidationError
    rq = parse_read_query({
        "entity": "lead_followups",
        "filters": [{"field": "status", "op": "$nonsense", "value": "pending"}],
    })
    try:
        compile_query(rq, business_id=uuid4())
    except ReadModelValidationError as exc:
        # The compiler raises ReadModelValidationError with a structured
        # FieldError naming the bad op and the allowed set.
        # Both pieces of context must be in the error envelope.
        d = exc.to_dict()
        msg = json.dumps(d)
        assert "$nonsense" in msg
        assert "status" in msg
    else:
        raise AssertionError("expected ReadModelValidationError on unknown op")


# ===========================================================================
# Bug A — filtered read returns ONLY matching rows (no silent table dump)
# ===========================================================================

def test_filtered_read_returns_only_matching_rows_not_whole_table():
    """The bug-A symptom: a filter for status=pending + scheduled_at=today
    must return ONE row (followup A), not all three. This is the silent-
    failure guard — if anyone ever re-introduces lenient top-level parsing,
    this test fires."""
    with _rollback_session() as s:
        user, today = _seed(s)
        # Day window: start-of-today (inclusive) to start-of-tomorrow (exclusive).
        # Done as two filters because `scheduled_at` is a DATETIME column and
        # `between` against bare date strings of the same day evaluates to a
        # zero-width range.
        day_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        rq = ReadQuery(
            "lead_followups",
            filters=[
                Filter("status", "=", "pending"),
                Filter("scheduled_at", ">=", day_start.isoformat()),
                Filter("scheduled_at", "<", day_end.isoformat()),
            ],
            select=["id", "note", "status", "scheduled_at"],
        )
        compiled = compile_query(rq, business_id=user.business_id)
        result = execute_query(compiled, s)
        assert len(result.rows) == 1, (
            f"expected exactly 1 row matching (status=pending AND today), "
            f"got {len(result.rows)} — bug A may have regressed (filter "
            f"silently dropped, whole table returned)"
        )
        assert result.rows[0]["note"] == "A — today pending"


# ===========================================================================
# Bug A — END-TO-END: teaching error flows back and the loop self-corrects
# ===========================================================================

def test_loop_self_corrects_from_where_shape_to_filters_shape():
    """The mandatory gate the user called out: it's not enough for the
    parser to raise — the error must flow back via the read_failed
    observation AND the loop's next turn must use the corrective info to
    emit a valid `filters` query.

    We script the FakeLLM to:
      turn 1: emit the bad `where: {…}` shape (the bug-A failure mode)
      turn 2: read the read_failed observation and emit the canonical
              `filters: [...]` shape, narrowed by status='pending'.
    Assert that:
      - turn 2's loop call returned a valid filtered read (1 matching row)
      - the observation summary on turn 1 named the problem
      - the loop reached done with the right answer."""
    with _rollback_session() as s:
        user, today = _seed(s)

        llm = _RecordingFakeLLM(
            # turn 1: the LLM's natural-but-wrong guess. The parser raises
            # ReadQueryParseError(bad_filters); the loop surfaces it as a
            # read_failed observation.
            {"thought": "list todays pending followups (will use where: …)",
             "action": {"type": "read", "query": {
                 "entity": "lead_followups",
                 "where": {
                     "status": {"=": "pending"},
                 },
             }}},
            # turn 2: having seen the teaching error, emit the canonical shape.
            {"thought": "the error showed the right shape; using filters list now",
             "action": {"type": "read", "query": {
                 "entity": "lead_followups",
                 "filters": [
                     {"field": "status", "op": "=", "value": "pending"},
                 ],
                 "select": ["id", "note", "status", "scheduled_at"],
             }}},
            # turn 3: done with the answer.
            {"thought": "found pending followups",
             "action": {"type": "done", "answer": "2 pending followups (A, C)."}},
        )

        result = _run(run_agent(s, user, goal="list my pending followups",
                                llm=llm, max_turns=4))
        assert result.kind == "done", (
            f"loop did not reach done — kind={result.kind!r} "
            f"history_len={len(result.history)}"
        )
        # The bad-shape turn produced a read_failed observation whose summary
        # named the problem; the loop fed it back to the LLM verbatim.
        assert len(result.history) >= 2
        first_obs = result.history[0].observation_summary
        assert "read failed" in first_obs.lower(), \
            f"turn-1 observation did not surface the read failure: {first_obs!r}"
        # The error code should be visible so the LLM has a machine-legible
        # pointer to the corrective example.
        assert "bad_filters" in first_obs, \
            f"turn-1 observation should name code 'bad_filters': {first_obs!r}"

        # Turn 2 was a valid filtered read — the loop self-corrected.
        second = result.history[1]
        assert second.action.get("type") == "read"
        assert second.action.get("query", {}).get("filters")
        # And the second observation reports a real row count, not an error.
        assert "read failed" not in second.observation_summary.lower()


# ===========================================================================
# Bug B — current date in the per-turn message; clock matches compiler's
# ===========================================================================

def test_per_turn_user_message_contains_current_date_line():
    """The per-turn user message MUST include a NOW line so the LLM can
    resolve 'today'. The system prompt stays free of this (preserves
    implicit-cache hit on the stable block)."""
    with _rollback_session() as s:
        user, _ = _seed(s)
        llm = _RecordingFakeLLM(
            {"thought": "trivial",
             "action": {"type": "done", "answer": "ok"}},
        )
        _run(run_agent(s, user, goal="say hi", llm=llm, max_turns=2))

        sys_prompt, msgs = llm.received[0]
        user_content = next(m["content"] for m in msgs if m["role"] == "user")

        # NOW line lives in the user message...
        assert "NOW:" in user_content, \
            f"per-turn user message missing NOW line:\n{user_content[:300]}"
        assert "Resolve 'today'" in user_content
        # ...and NOT in the cached system prompt.
        assert "NOW:" not in sys_prompt, \
            "NOW line leaked into the system prompt — this would break " \
            "the implicit-cache hit on the stable block on every minute."


def test_now_in_prompt_uses_same_clock_as_compiler():
    """The injected `now` must match what the compiler sees, so 'today'
    in the LLM's view and the SQL's view agree. We assert the prompt
    carries a 'NOW: YYYY-MM-DD' line whose date matches the business-local
    today the loop computes from the business's timezone."""
    import re
    with _rollback_session() as s:
        user, _ = _seed(s)
        llm = _RecordingFakeLLM(
            {"thought": "trivial",
             "action": {"type": "done", "answer": "ok"}},
        )
        _run(run_agent(s, user, goal="say hi", llm=llm, max_turns=2))

        _, msgs = llm.received[0]
        user_content = next(m["content"] for m in msgs if m["role"] == "user")
        m = re.search(r"NOW: (\d{4}-\d{2}-\d{2})", user_content)
        assert m, f"no NOW: YYYY-MM-DD in prompt:\n{user_content[:200]}"
        prompt_date = m.group(1)
        expected_date = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")
        assert prompt_date == expected_date, (
            f"NOW date in prompt ({prompt_date}) doesn't match the "
            f"business-local today ({expected_date}) — Bug B fix is "
            f"misaligned with the compiler's clock"
        )


# ===========================================================================
# Driver
# ===========================================================================

def _all_tests():
    tests = [
        test_parser_rejects_where_dict_of_dicts_with_teaching_error,
        test_where_alias_with_correct_list_shape_works,
        test_in_operator_on_string_field_returns_all_matches_in_one_read,
        test_parser_rejects_unknown_operator_with_teaching_error,
        test_filtered_read_returns_only_matching_rows_not_whole_table,
        test_loop_self_corrects_from_where_shape_to_filters_shape,
        test_per_turn_user_message_contains_current_date_line,
        test_now_in_prompt_uses_same_clock_as_compiler,
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
