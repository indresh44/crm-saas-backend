"""The ReAct loop.

One LLM call per turn. The LLM returns one structured action. The loop executes
it, summarizes, and either continues, stops at a prepare (awaiting human
confirm), exits with done, asks the user, or hits the step budget.

Structurally:
  - PREPARE always returns immediately with kind="awaiting_confirm". There is no
    code path from prepare -> done in the same run. (Per spec.)
  - Two consecutive identical actions -> hard stop with kind="error". (Per spec.)
  - A prepare whose UUID inputs are not all in prior-read provenance gets
    REJECTED LOCALLY (no layer-2 call) and the loop continues with a "re-read"
    observation. (Per spec.)
  - Step budget is a hard cap. Reaching it without a terminal action -> kind="exhausted".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlmodel import Session

from app.agent.accounting import TokenReport, _TokenReportBuilder
from app.agent.actions import Action, ActionParseError, action_signature, parse_action_json
from app.agent.observations import (
    TurnRecord,
    collect_known_uuids,
    find_unknown_uuid_inputs,
    summarize_prepare_error,
    summarize_prepare_success,
    summarize_read_error,
    summarize_read_success,
)
from app.agent.parsing import ReadQueryParseError, extract_json_object, parse_read_query
from app.agent.prompts import (
    build_capability_context,
    build_schema_context,
    build_system_prompt,
    render_history,
)
from app.models.business import Business
from app.models.user import User
from app.read_model.compiler import ReadModelValidationError, compile_query
from app.read_model.executor import execute_query
from app.services.llm_service import LLMService
from app.write_surface.engine import WriteSurfaceError, prepare as ws_prepare


MAX_TURNS = 8


@dataclass(frozen=True)
class AgentRunResult:
    kind: Literal["done", "awaiting_confirm", "ask_user", "exhausted", "error"]
    history: tuple[TurnRecord, ...]
    tokens: TokenReport
    answer: Optional[str] = None              # for kind=done
    prepared_action_id: Optional[str] = None  # for kind=awaiting_confirm
    preview: Optional[str] = None             # for kind=awaiting_confirm
    editable_fields: tuple[str, ...] = ()     # for kind=awaiting_confirm
    question: Optional[str] = None            # for kind=ask_user
    error: Optional[dict] = None              # for kind=error or exhausted (info)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "answer": self.answer,
            "prepared_action_id": self.prepared_action_id,
            "preview": self.preview,
            "editable_fields": list(self.editable_fields),
            "question": self.question,
            "error": self.error,
            "history": [
                {
                    "turn": t.turn, "thought": t.thought, "action": t.action,
                    "observation_summary": t.observation_summary,
                    "observation_raw": t.observation_raw,
                }
                for t in self.history
            ],
            "tokens": self.tokens.to_dict(),
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_agent(
    session: Session,
    current_user: User,
    goal: str,
    *,
    max_turns: int = MAX_TURNS,
    llm: Optional[LLMService] = None,
    prior_history: tuple[TurnRecord, ...] = (),
) -> AgentRunResult:
    """One run = one user goal. Stops on done | awaiting_confirm | ask_user | error |
    exhausted. Sequential prepare/confirm: chained writes are separate runs.

    `prior_history` is the history tuple from a previous run_agent call for THE
    SAME task — used to give the LLM in-task continuity (Stage 1 memory). It is
    appended to the live history list so:
      - the LLM sees prior turns labelled "(PRIOR CALL — already completed)";
      - UUIDs discovered in earlier reads still satisfy the prepare-time
        provenance check (raw stays in process memory; only summaries reach
        the LLM, same anti-contamination rule the loop already uses);
      - the returned `history` is the full transcript (prior + new), ready to
        be fed back in next call.
    Turn numbers continue from `len(prior_history) + 1` so the transcript reads
    monotonically. The step budget is FRESH per call. The repeated-action guard
    is also fresh — it's a runaway-LLM check, not a cross-call check."""
    biz = session.get(Business, current_user.business_id)
    now = _business_local_now(biz)

    schema_ctx = build_schema_context()
    capability_ctx = build_capability_context()
    system_prompt = build_system_prompt(schema_ctx, capability_ctx)

    llm = llm or LLMService()
    history: list[TurnRecord] = list(prior_history)
    prior_count = len(prior_history)
    tokens = _TokenReportBuilder()
    last_signature: Optional[str] = None

    first_turn = prior_count + 1
    for turn in range(first_turn, first_turn + max_turns):
        messages = render_history(goal, history, prior_count=prior_count)

        # --- LLM call (with one JSON-parse retry) ---
        try:
            thought, action = await _llm_pick_action(
                llm, system_prompt, messages, turn=turn, tokens=tokens,
            )
        except _BadActionJSON as exc:
            return AgentRunResult(
                kind="error", history=tuple(history), tokens=tokens.build(),
                error={"code": "bad_action_json", "message": exc.message, "raw": exc.raw[:500]},
            )

        # --- Runaway guard: identical-consecutive action -> hard stop ---
        sig = action_signature(action)
        if last_signature is not None and sig == last_signature:
            return AgentRunResult(
                kind="error", history=tuple(history), tokens=tokens.build(),
                error={"code": "repeated_action",
                       "message": "the LLM emitted the same action two turns in a row — aborting",
                       "action": action.raw},
            )
        last_signature = sig

        # --- Dispatch ---
        if action.type == "done":
            return AgentRunResult(
                kind="done", history=tuple(history), tokens=tokens.build(),
                answer=str(action.raw["answer"]),
            )

        if action.type == "ask_user":
            return AgentRunResult(
                kind="ask_user", history=tuple(history), tokens=tokens.build(),
                question=str(action.raw["question"]),
            )

        if action.type == "read":
            summary, raw = _execute_read(
                session, current_user.business_id, now, action.raw.get("query") or {},
            )
            history.append(TurnRecord(
                turn=turn, thought=thought, action=action.raw,
                observation_summary=summary, observation_raw=raw,
            ))
            continue

        if action.type == "prepare":
            # UUID-provenance check BEFORE calling layer 2.
            inputs = action.raw.get("inputs") or {}
            unknown = find_unknown_uuid_inputs(inputs, collect_known_uuids(history))
            if unknown:
                summary, raw = _uuid_blocked_observation(unknown)
                history.append(TurnRecord(
                    turn=turn, thought=thought, action=action.raw,
                    observation_summary=summary, observation_raw=raw,
                ))
                continue   # loop continues; LLM should re-read

            # Call layer 2 prepare(). On success the loop STOPS — by structure,
            # there is no path from here to a `done` action in the same run.
            try:
                handle = ws_prepare(
                    session, current_user,
                    capability_name=str(action.raw["capability"]),
                    raw_inputs=dict(inputs),
                )
            except WriteSurfaceError as exc:
                summary, raw = summarize_prepare_error(exc.to_dict())
                history.append(TurnRecord(
                    turn=turn, thought=thought, action=action.raw,
                    observation_summary=summary, observation_raw=raw,
                ))
                continue

            summary, raw = summarize_prepare_success(
                capability=str(action.raw["capability"]),
                action_id=handle.id, preview=handle.preview,
                editable_fields=list(handle.editable_fields),
            )
            history.append(TurnRecord(
                turn=turn, thought=thought, action=action.raw,
                observation_summary=summary, observation_raw=raw,
            ))
            return AgentRunResult(
                kind="awaiting_confirm", history=tuple(history), tokens=tokens.build(),
                prepared_action_id=handle.id, preview=handle.preview,
                editable_fields=tuple(handle.editable_fields),
            )

    # Hit the step budget without a terminal action.
    return AgentRunResult(
        kind="exhausted", history=tuple(history), tokens=tokens.build(),
        error={"code": "step_budget_exhausted",
               "message": f"reached the {max_turns}-turn cap without a final action"},
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

class _BadActionJSON(Exception):
    def __init__(self, message: str, raw: str):
        self.message = message
        self.raw = raw or ""


async def _llm_pick_action(
    llm: LLMService, system_prompt: str, messages: list[dict],
    *, turn: int, tokens: _TokenReportBuilder,
) -> tuple[str, Action]:
    """One LLM call (with one JSON-parse retry that feeds the error back).
    Records token usage on the builder. Raises _BadActionJSON on both attempts."""
    resp = await llm.chat(system_prompt=system_prompt, messages=messages)
    tokens.add(turn, resp.model, resp.input_tokens, resp.output_tokens)
    raw = resp.content or ""
    try:
        return parse_action_json(extract_json_object(raw))
    except (ValueError, ActionParseError) as exc:
        retry_msgs = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user",
             "content": f"That output was not valid JSON in the required envelope: {exc}. "
                        "Emit ONLY the JSON envelope, nothing else."},
        ]
        resp2 = await llm.chat(system_prompt=system_prompt, messages=retry_msgs)
        tokens.add(turn, resp2.model, resp2.input_tokens, resp2.output_tokens)
        raw2 = resp2.content or ""
        try:
            return parse_action_json(extract_json_object(raw2))
        except (ValueError, ActionParseError) as exc2:
            raise _BadActionJSON(str(exc2), raw2)


def _execute_read(session: Session, business_id: UUID, now: datetime,
                  q_json: dict) -> tuple[str, dict]:
    try:
        rq = parse_read_query(q_json)
        compiled = compile_query(rq, business_id=business_id, now=now)
        result = execute_query(compiled, session)
    except ReadQueryParseError as exc:
        # Specific, machine-legible: the parser already named the bad key and
        # suggested the right one. Pass it through unchanged so the LLM sees it.
        return summarize_read_error(exc.to_dict())
    except ReadModelValidationError as exc:
        return summarize_read_error(exc.to_dict())
    except Exception as exc:  # noqa: BLE001 — surface any runtime error as a read failure
        return summarize_read_error({"code": "read_failed", "message": str(exc)})
    entity = q_json.get("entity", "<unknown>")
    return summarize_read_success(entity, rows=result.rows)


def _uuid_blocked_observation(unknown: list[tuple[str, str]]) -> tuple[str, dict]:
    fields_str = ", ".join(f"{k}={v}" for k, v in unknown)
    summary = (
        f"prepare BLOCKED — UUID-shaped input(s) not seen in any prior read: "
        f"{fields_str}. That id wasn't in any read result — re-read to confirm it exists "
        f"before preparing."
    )
    raw = {"kind": "uuid_not_in_history",
           "unknown": [{"field": k, "value": v} for k, v in unknown]}
    return summary, raw


def _business_local_now(business: Optional[Business]) -> datetime:
    tzname = business.timezone if business and business.timezone else "Asia/Kolkata"
    return datetime.now(ZoneInfo(tzname))
