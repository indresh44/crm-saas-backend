"""Orchestration around the stateless agent loop.

Wires `run_agent` (in-memory continuity) to `agent_chat_sessions` (persistent
continuity across HTTP requests):

    load session  ->  run_agent(prior_history=...)  ->  save updated history

The session row owns `continuity_history` (the LLM-visible memory) and
`awaiting_action_id` (one-bit state telling us a prepare is mid-air). The
message table is the user-facing transcript and the debug-panel data.

Every commit-result and synthesised-turn raw payload is funnelled through
`_safe_jsonify` BEFORE it touches storage — that's the JSON-safety boundary
the plan committed to (a Decimal in continuity_history would crash the JSONB
write and leave the row inconsistent)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.agent.confirm import ConfirmResult, confirm_prepared_action
from app.agent.loop import AgentRunResult, run_agent
from app.agent.observations import TurnRecord
from app.agent.serialize import (
    _safe_jsonify,
    synth_cancel_record,
    synth_commit_record,
    turn_from_dict,
    turn_to_dict,
    turn_to_dict_no_raw,
)
from app.models.agent_chat import AgentChatMessage, AgentChatSession
from app.models.user import User
from app.repositories import agent_chat_repo
from app.services.llm_service import LLMService


# ---------------------------------------------------------------------------
# Message kinds (the renderer keys on these)
# ---------------------------------------------------------------------------

MessageKind = Literal[
    "text",                # user message
    "done",                # assistant final answer (no write needed)
    "ask_user",            # assistant asks a question
    "awaiting_confirm",    # assistant prepared a write; user must confirm
    "commit_result",       # assistant: a write just landed
    "cancelled",           # assistant: a prepare was cancelled by user
    "error",               # loop returned an error (or commit failed)
    "exhausted",           # loop hit the step budget
]


# ---------------------------------------------------------------------------
# Envelope (uniform across /message, /confirm, /cancel)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AssistantEnvelope:
    session_id: UUID
    message_id: UUID
    kind: str
    content: Optional[str]
    payload: dict[str, Any]
    turn_detail: list[dict[str, Any]]
    tokens: Optional[dict[str, Any]]
    created_at: str
    awaiting_action_id: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": str(self.session_id),
            "message_id": str(self.message_id),
            "kind": self.kind,
            "content": self.content,
            "payload": self.payload,
            "turn_detail": self.turn_detail,
            "tokens": self.tokens,
            "created_at": self.created_at,
            "awaiting_action_id": self.awaiting_action_id,
        }


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def create_session(
    session: Session, current_user: User, *, title: Optional[str] = None,
) -> AgentChatSession:
    return agent_chat_repo.create_session(
        session,
        business_id=current_user.business_id,
        user_id=current_user.id,
        title=title,
    )


def list_sessions(
    session: Session, current_user: User, *, limit: int = 50,
) -> list[AgentChatSession]:
    return agent_chat_repo.list_sessions(
        session,
        business_id=current_user.business_id,
        user_id=current_user.id,
        limit=limit,
    )


def get_session_with_messages(
    session: Session, current_user: User, session_id: UUID,
) -> tuple[AgentChatSession, list[AgentChatMessage]]:
    row = agent_chat_repo.get_session(
        session,
        session_id=session_id,
        business_id=current_user.business_id,
        user_id=current_user.id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="agent chat session not found")
    messages = agent_chat_repo.list_messages(session, session_id=row.id)
    return row, messages


# ---------------------------------------------------------------------------
# /message handler
# ---------------------------------------------------------------------------

async def handle_message(
    session: Session, current_user: User, *,
    session_id: UUID, message_text: str,
    llm: Optional[LLMService] = None,
) -> AssistantEnvelope:
    """Persist user message -> load prior history -> run_agent -> save updated
    history + assistant message. The session row is locked FOR UPDATE for the
    duration; concurrent /message or /confirm on the same session wait.

    DELIBERATE NON-DEDUPE (do not "fix" this later): we persist the user
    message BEFORE running the LLM, so a mid-call crash leaves a row with no
    assistant follow-up. On retry, the user message will appear twice in the
    transcript. That's the lesser evil — deduping by content would silently
    drop a legitimate "yes please do that again" follow-up. The UI shows the
    user message immediately so the user has the signal to decide whether to
    resend; we never decide for them."""
    row = agent_chat_repo.get_session_for_update(
        session,
        session_id=session_id,
        business_id=current_user.business_id,
        user_id=current_user.id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="agent chat session not found")
    if row.awaiting_action_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("session is awaiting confirm — call /confirm or /cancel "
                    "before sending another message"),
        )

    # 1. user message — persisted first; see DELIBERATE NON-DEDUPE above.
    agent_chat_repo.add_message(
        session, session_id=row.id, role="user", kind="text", content=message_text,
    )
    if not row.title:
        row.title = _derive_title(message_text)

    # 2. load prior history and run the loop
    prior = tuple(turn_from_dict(d) for d in (row.continuity_history or []))
    prior_count = len(prior)
    result = await run_agent(
        session, current_user, message_text, prior_history=prior, llm=llm,
    )

    # 3. save updated history + assistant message; commit ONCE at the end.
    return _persist_and_envelope_from_result(
        session, row, result, prior_count=prior_count,
    )


# ---------------------------------------------------------------------------
# /confirm handler
# ---------------------------------------------------------------------------

def handle_confirm(
    session: Session, current_user: User, *,
    session_id: UUID, prepared_action_id: str,
    edits: Optional[dict[str, Any]] = None,
) -> AssistantEnvelope:
    """Commit the prepared action the session is awaiting. Refuses any other
    action id — this is the outer fence around layer-2's atomic claim."""
    row = agent_chat_repo.get_session_for_update(
        session,
        session_id=session_id,
        business_id=current_user.business_id,
        user_id=current_user.id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="agent chat session not found")
    if not row.awaiting_action_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="session is not awaiting a confirm",
        )
    if row.awaiting_action_id != prepared_action_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("prepared_action_id does not match this session's pending "
                    "action"),
        )

    cr = confirm_prepared_action(
        session, current_user, action_id=prepared_action_id, edits=edits or {},
    )

    history = [turn_from_dict(d) for d in (row.continuity_history or [])]
    capability = _capability_from_last_prepare(history)

    if cr.ok:
        committed = synth_commit_record(
            history,
            prepared_action_id=prepared_action_id,
            capability=capability,
            cr=cr,
        )
        history.append(committed)
        row.continuity_history = [turn_to_dict(t) for t in history]
        row.awaiting_action_id = None

        safe_result = _safe_jsonify(cr.result)
        msg = agent_chat_repo.add_message(
            session,
            session_id=row.id, role="assistant", kind="commit_result",
            content=_summarize_commit(capability, safe_result),
            payload={"prepared_action_id": prepared_action_id,
                     "capability": capability,
                     "commit_result": safe_result},
            turn_detail=[turn_to_dict_no_raw(committed)],
            tokens=None,
        )
        session.commit()
        return _envelope_from_message(row, msg)

    # commit failed: keep awaiting_action_id so the user can retry confirm OR
    # cancel; mirror ask.py's "stop and let the user try again" stance.
    safe_error = _safe_jsonify(cr.error or {})
    msg = agent_chat_repo.add_message(
        session,
        session_id=row.id, role="assistant", kind="error",
        content=str(safe_error.get("message") or "commit failed"),
        payload={"prepared_action_id": prepared_action_id,
                 "capability": capability,
                 "error": safe_error},
        turn_detail=None, tokens=None,
    )
    session.commit()
    return _envelope_from_message(row, msg)


# ---------------------------------------------------------------------------
# /cancel handler
# ---------------------------------------------------------------------------

def handle_cancel(
    session: Session, current_user: User, *,
    session_id: UUID, prepared_action_id: str,
) -> AssistantEnvelope:
    """User declined the awaiting prepare. We APPEND a synthetic CANCELLED
    turn to continuity_history — not just leave the bare prepare behind —
    so the LLM's next call sees the rejection and doesn't blindly re-emit
    the identical prepare. (See risk-3 wording in §3 of the system prompt.)"""
    row = agent_chat_repo.get_session_for_update(
        session,
        session_id=session_id,
        business_id=current_user.business_id,
        user_id=current_user.id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="agent chat session not found")
    if not row.awaiting_action_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="session is not awaiting a confirm",
        )
    if row.awaiting_action_id != prepared_action_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="prepared_action_id does not match this session's pending action",
        )

    history = [turn_from_dict(d) for d in (row.continuity_history or [])]
    capability = _capability_from_last_prepare(history)
    cancelled_turn = synth_cancel_record(
        history,
        prepared_action_id=prepared_action_id,
        capability=capability,
    )
    history.append(cancelled_turn)
    row.continuity_history = [turn_to_dict(t) for t in history]
    row.awaiting_action_id = None

    msg = agent_chat_repo.add_message(
        session,
        session_id=row.id, role="assistant", kind="cancelled",
        content="Cancelled. Tell me what to change, or ask for something else.",
        payload={"prepared_action_id": prepared_action_id,
                 "capability": capability},
        turn_detail=[turn_to_dict_no_raw(cancelled_turn)],
        tokens=None,
    )
    session.commit()
    return _envelope_from_message(row, msg)


# ---------------------------------------------------------------------------
# Shared: build envelope + persist after a run_agent call
# ---------------------------------------------------------------------------

def _persist_and_envelope_from_result(
    session: Session, row: AgentChatSession,
    result: AgentRunResult, *, prior_count: int,
) -> AssistantEnvelope:
    new_turns = result.history[prior_count:]
    # Always persist the FULL transcript (prior + new) — see why on the
    # session model docstring.
    row.continuity_history = [turn_to_dict(t) for t in result.history]
    row.awaiting_action_id = (
        result.prepared_action_id if result.kind == "awaiting_confirm" else None
    )

    kind, content, payload = _classify_result(result)
    msg = agent_chat_repo.add_message(
        session,
        session_id=row.id, role="assistant", kind=kind,
        content=content, payload=payload,
        turn_detail=[turn_to_dict_no_raw(t) for t in new_turns],
        tokens=result.tokens.to_dict(),
    )
    session.commit()
    return _envelope_from_message(row, msg)


def _classify_result(result: AgentRunResult) -> tuple[str, Optional[str], dict[str, Any]]:
    """Map AgentRunResult -> (message kind, content, payload). All payload
    values pass through _safe_jsonify on the caller side via add_message ->
    JSONB. The result's own dicts (answer, preview, question, error) are
    primitive strings/dicts so safe by construction; we still funnel them
    through _safe_jsonify on the error path because error.details may carry
    arbitrary capability-side values."""
    if result.kind == "done":
        return "done", result.answer, {}
    if result.kind == "ask_user":
        return "ask_user", result.question, {"question": result.question}
    if result.kind == "awaiting_confirm":
        return "awaiting_confirm", result.preview, {
            "prepared_action_id": result.prepared_action_id,
            "preview": result.preview,
            "editable_fields": list(result.editable_fields),
        }
    if result.kind in ("error", "exhausted"):
        safe_err = _safe_jsonify(result.error or {})
        return result.kind, str(safe_err.get("message") or result.kind), {"error": safe_err}
    # Defensive — should never happen given the loop's Literal type.
    return "error", f"unknown loop kind: {result.kind!r}", {}


def _envelope_from_message(row: AgentChatSession, msg: AgentChatMessage) -> AssistantEnvelope:
    return AssistantEnvelope(
        session_id=row.id,
        message_id=msg.id,
        kind=msg.kind,
        content=msg.content,
        payload=msg.payload or {},
        turn_detail=msg.turn_detail or [],
        tokens=msg.tokens,
        created_at=msg.created_at.isoformat(),
        awaiting_action_id=row.awaiting_action_id,
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _derive_title(first_message: str) -> str:
    """First line, trimmed to ~80 chars. Stored once per session; rename UI
    is out of scope for v1."""
    line = (first_message or "").strip().splitlines()[0] if first_message else ""
    return line[:80] if line else "Untitled chat"


def _capability_from_last_prepare(history: list[TurnRecord]) -> str:
    """Walk backwards for the most recent prepare. The awaiting prepare is
    typically the last entry, but be defensive in case the harness appended
    something after (it shouldn't, but a future change might)."""
    for t in reversed(history):
        if t.action.get("type") == "prepare":
            return str(t.action.get("capability") or "")
    return ""


def _summarize_commit(capability: str, safe_result: Any) -> str:
    """One-line user-facing confirmation. The full payload sits in
    payload['commit_result'] for the debug panel."""
    return f"Done — {capability or 'write'} committed."
