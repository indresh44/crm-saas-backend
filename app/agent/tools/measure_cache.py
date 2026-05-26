"""Step 1 measurement script — baseline `cached_tokens` against the real
Gemini API, before any explicit-cache code is written.

Goal: per-turn observation of `(input_tokens, cached_tokens, output_tokens)`
across a multi-turn run, so we can answer:

  Is Gemini implicit caching ALREADY firing on the agent loop's request shape?

If turns 2+ show meaningful cached_tokens, Step 3 (explicit caching) is
likely unnecessary. If they stay at 0, we proceed to Step 3.

This is NOT a unit test:
  * Talks to the REAL Gemini API (uses real tokens, costs real money).
  * Requires GOOGLE_API_KEY in the environment.
  * Seeds a throwaway in-memory dataset; no DB writes outside a rolled-back
    savepoint.
  * Idempotent — every run rebuilds the seed and rolls back at the end.

Usage:
    python -m app.agent.tools.measure_cache

Output: a per-turn table + totals, printed to stdout.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import contextmanager
from uuid import uuid4

from sqlalchemy import event
from sqlmodel import Session

from app.agent.loop import run_agent
from app.core.database import engine
from app.core.phone import normalize_phone_value
from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import UserRole
from app.models.lead import Lead
from app.models.pipeline import Pipeline, PipelineStage
from app.models.user import User
from app.services.llm_service import LLMService


# ---------------------------------------------------------------------------
# Savepoint-rollback session (same pattern as the test suites)
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


def _seed(session: Session) -> User:
    """Throwaway tenant + a handful of customers/leads so the loop has real
    rows to read against. Returns the owner User."""
    bid = uuid4()
    session.add(Business(id=bid, name="MeasureCache Co", phone="9990000000"))
    session.flush()
    user = User(
        id=uuid4(), business_id=bid, name="Owner",
        email=f"owner-{bid}@measure.local",
        role=UserRole.OWNER, is_active=True,
    )
    session.add(user); session.flush()

    pid = uuid4()
    session.add(Pipeline(id=pid, business_id=bid, name="Default", is_default=True))
    session.flush()
    sid = uuid4()
    session.add(PipelineStage(id=sid, pipeline_id=pid, name="New",
                              position=1, color="#888"))
    session.flush()

    for i, name in enumerate(["Rajesh Mehta", "Anjali Sharma", "Suresh Patel"]):
        cid = uuid4()
        phone = f"+91 99999 {10000 + i:05d}"
        session.add(Customer(
            id=cid, business_id=bid, name=name,
            phone=phone, phone_normalized=normalize_phone_value(phone),
        ))
    session.flush()
    return user


# ---------------------------------------------------------------------------
# Reporting helper
# ---------------------------------------------------------------------------

def _print_table(label: str, report) -> None:
    print()
    print(f"=== {label} ===")
    print(f"{'turn':>4} {'model':<32} {'input':>8} {'cached':>8} {'output':>8} "
          f"{'cache%':>7}")
    print("-" * 80)
    for t in report.per_turn:
        pct = (100.0 * t.cached_tokens / t.input_tokens) if t.input_tokens else 0.0
        print(f"{t.turn:>4} {t.model[:32]:<32} {t.input_tokens:>8} "
              f"{t.cached_tokens:>8} {t.output_tokens:>8} {pct:>6.1f}%")
    print("-" * 80)
    total_pct = (
        100.0 * report.total_cached_tokens / report.total_input_tokens
        if report.total_input_tokens else 0.0
    )
    print(f"     {'TOTAL':<32} {report.total_input_tokens:>8} "
          f"{report.total_cached_tokens:>8} {report.total_output_tokens:>8} "
          f"{total_pct:>6.1f}%")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

async def _measure_once(goal: str) -> None:
    """Run one multi-turn task and print its token table."""
    llm = LLMService()
    with _rollback_session() as s:
        user = _seed(s)
        s.commit()
        print(f"\n--- GOAL: {goal!r} ---")
        result = await run_agent(s, user, goal=goal, llm=llm, max_turns=8)
        print(f"--- result.kind={result.kind!r} "
              f"answer={(result.answer or '')[:80]!r} ---")
        _print_table(f"per-turn tokens — {result.kind}", result.tokens)


async def _main() -> int:
    # llm_settings reads .env via pydantic-settings; either source is fine.
    from app.config.llm_config import llm_settings
    has_key = bool(
        llm_settings.google_api_key
        or llm_settings.llm_api_key
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("LLM_API_KEY")
    )
    if not has_key:
        print("ERROR: GOOGLE_API_KEY (or LLM_API_KEY) not set — cannot "
              "measure against the real Gemini API.", file=sys.stderr)
        return 2

    # Goal designed to provoke a multi-turn (read -> read -> done) trajectory.
    # The exact turn count varies a little with model behaviour; we don't
    # depend on a specific count, we just need >= 2 turns to see caching.
    goals = [
        "List the customers in my system and tell me how many there are.",
    ]
    for g in goals:
        await _measure_once(g)

    print()
    print("Interpretation guide:")
    print("  * Turn 1 cached% near 0 — expected; first call seeds the cache.")
    print("  * Turns 2+ cached% high (~70-95%) — implicit caching is firing;")
    print("    Step 3 (explicit caching) likely not needed.")
    print("  * Turns 2+ cached% at 0 — implicit caching NOT firing; proceed")
    print("    to Step 3.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
