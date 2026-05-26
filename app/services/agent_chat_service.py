"""Orchestration around the multi-task runner.

Wires `run_message_as_tasks` (one or more `run_agent` calls behind a splitter)
to `agent_chat_sessions` / `agent_chat_messages` / `agent_tasks`:

    POST /message  -> persist user msg -> run splitter+runner -> persist one
                       assistant message describing the batch outcome

    POST /confirm  -> find the task by prepared_action_id -> commit ->
                       append synth COMMITTED turn to THAT TASK's
                       continuity_history -> resume run_agent on the task ->
                       persist one assistant message with the task's new state

    POST /cancel   -> same but synth CANCELLED + mark task DONE (cancelled)
                       without resuming

Multi-task vs single-task surface:
  * Single-task batch (the common case) — the assistant message uses the
    existing MessageKind values (done | ask_user | awaiting_confirm | error |
    exhausted) exactly as before. Frontend renders unchanged.

  * Multi-task batch (>1 task) — the assistant message uses the new
    `multi_task` kind. The payload carries a `tasks` array; each entry has
    `task_id`, `description`, and the same per-task fields the single-task
    kinds use. The frontend stacks one card per task IN ORDER and LABELS
    each card with its description so the owner cannot confirm the wrong one.

Continuity rewiring:
  * `agent_chat_sessions.continuity_history` is NO LONGER read or written by
    the new flow. It remains on the table for backwards compatibility with
    sessions created before 0041. Each AgentTask owns its own
    `continuity_history`, fresh per task.

  * `agent_chat_sessions.awaiting_action_id` is NO LONGER set or checked.
    The /message gate is removed — a session can have multiple awaiting
    tasks at once (each captured on the task row). /confirm + /cancel target
    a task by `pending_action_id` directly.

Every commit_result and synthesised-turn raw payload is funnelled through
`_safe_jsonify` BEFORE it touches storage — same JSON-safety boundary the
old service relied on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.agent.confirm import confirm_prepared_action
from app.agent.multi_task_runner import (
    TaskBatchResult,
    apply_cancel_to_task,
    resume_task_after_commit,
    run_message_as_tasks,
)
from app.agent.serialize import (
    _safe_jsonify,
    synth_cancel_record,
    synth_commit_record,
    turn_from_dict,
    turn_to_dict,
    turn_to_dict_no_raw,
)
from app.models.agent_chat import AgentChatMessage, AgentChatSession
from app.models.agent_task import AgentTask, TaskStatus
from app.models.user import User
from app.repositories import agent_chat_repo, agent_task_repo
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
    "multi_task",          # assistant: per-task outcomes (batch with >1 task)
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
    # Deprecated for new flow but kept on the envelope so existing API clients
    # don't break. Always None for new-flow responses.
    awaiting_action_id: Optional[str]
    # Multi-task locators — set on /confirm and /cancel responses (which always
    # target a specific task) and on the initial multi_task response (where
    # batch_id is set but task_id is None — the task list is in payload).
    batch_id: Optional[UUID] = None
    task_id: Optional[UUID] = None

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
            "batch_id": str(self.batch_id) if self.batch_id else None,
            "task_id": str(self.task_id) if self.task_id else None,
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
) -> tuple[AgentChatSession, list[AgentChatMessage], bool]:
    """Return (session_row, messages, has_unresolved_action).

    `has_unresolved_action` is True iff this session has at least one task
    currently in awaiting_approval with pending_action_id NOT NULL — i.e. a
    real prepared write still waiting on the owner. Used by the chat client
    to gate the message input (don't let the owner send a new /message
    while a confirm is mid-air).

    Each `awaiting_confirm` message in the returned list is also enriched
    in-place with `payload['resolution']` so the client can render
    already-resolved cards as disabled / labelled rather than as live
    buttons. Resolution values:
      * None         — still pending (the task is still awaiting_approval
                        with the same pending_action_id)
      * 'confirmed'  — task moved to DONE; result.kind == 'done' (the
                        commit ran)
      * 'cancelled'  — task moved to DONE; result.kind == 'cancelled'
      * 'dismissed'  — task moved to DONE; result.kind == 'dismissed'
                        (the new dismiss-ask-user path, also a fallback
                        if an awaiting_confirm message's task is dismissed
                        somehow)
      * 'failed'     — task moved to FAILED (commit attempted but errored)
      * 'unknown'    — task can't be found anymore; treat as resolved so
                        the live buttons don't appear (frontend safety)

    Belt-and-braces: we only inspect tasks belonging to this session, and
    every task lookup is implicitly tenant-scoped through `session_id`
    (which was fetched with business_id + user_id above)."""
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

    # Build a one-shot index of this session's tasks. One query, no N+1.
    from app.models.agent_task import AgentTask, TaskStatus
    from sqlmodel import select as _select
    session_tasks = list(session.exec(
        _select(AgentTask).where(AgentTask.session_id == row.id)
    ).all())
    tasks_by_id = {str(t.id): t for t in session_tasks}

    has_unresolved_action = any(
        t.status == TaskStatus.AWAITING_APPROVAL.value
        and t.pending_action_id is not None
        for t in session_tasks
    )

    # Enrich each awaiting_confirm message with the current resolution. We
    # MUTATE the payload dict in place — these AgentChatMessage rows are
    # about to be serialised to the response, not persisted. (SQLAlchemy
    # would only re-flush them if they were dirty; we don't change the
    # column type, so no accidental write happens.)
    for msg in messages:
        if msg.kind == "awaiting_confirm":
            payload = dict(msg.payload or {})
            task_id_str = payload.get("task_id")
            prepared_action_id = payload.get("prepared_action_id")
            payload["resolution"] = _resolve_awaiting_message(
                task_id_str=task_id_str,
                prepared_action_id=prepared_action_id,
                tasks_by_id=tasks_by_id,
            )
            msg.payload = payload
            continue

        # Multi-task messages carry their per-slot prepared_action_id +
        # task_id INSIDE payload.tasks[]. Same enrichment pass at the
        # slot level so the frontend's per-slot AwaitingConfirmCard can
        # render disabled / labelled when a slot has already been
        # resolved through another surface (dashboard carousel, parallel
        # tab, dismiss). Without this, the multi-task batch in chat
        # stays stuck on "awaiting your approval" forever even after
        # the owner clears it elsewhere.
        if msg.kind == "multi_task":
            payload = dict(msg.payload or {})
            raw_tasks = payload.get("tasks") or []
            if isinstance(raw_tasks, list):
                enriched_slots: list[dict] = []
                for slot in raw_tasks:
                    if not isinstance(slot, dict):
                        enriched_slots.append(slot)
                        continue
                    if slot.get("kind") == "awaiting_confirm":
                        new_slot = dict(slot)
                        new_slot["resolution"] = _resolve_awaiting_message(
                            task_id_str=slot.get("task_id"),
                            prepared_action_id=slot.get("prepared_action_id"),
                            tasks_by_id=tasks_by_id,
                        )
                        enriched_slots.append(new_slot)
                    else:
                        enriched_slots.append(slot)
                payload["tasks"] = enriched_slots
                msg.payload = payload
            continue

    return row, messages, has_unresolved_action


def _resolve_awaiting_message(
    *,
    task_id_str: Optional[str],
    prepared_action_id: Optional[str],
    tasks_by_id: dict[str, "AgentTask"],
) -> Optional[str]:
    """Compute the resolution label for one awaiting_confirm message.
    Returns None if the underlying task is still actively waiting on
    this exact prepared_action_id; otherwise a string label."""
    from app.models.agent_task import TaskStatus
    if not task_id_str:
        # Older messages may not have task_id in the payload (pre-fix
        # multi-task changes). Without it we can't disambiguate which
        # task this card belongs to — treat as unknown so the frontend
        # disables the buttons rather than offering a stale confirm.
        return "unknown"
    task = tasks_by_id.get(str(task_id_str))
    if task is None:
        return "unknown"

    # Still actively waiting on THIS prepared action — live buttons OK.
    if (
        task.status == TaskStatus.AWAITING_APPROVAL.value
        and task.pending_action_id == prepared_action_id
        and prepared_action_id is not None
    ):
        return None

    # Task has moved on. Classify by the result it landed in.
    result = task.result or {}
    rk = result.get("kind") if isinstance(result, dict) else None
    if task.status == TaskStatus.FAILED.value:
        return "failed"
    if rk == "cancelled":
        return "cancelled"
    if rk == "dismissed":
        return "dismissed"
    if task.status == TaskStatus.DONE.value:
        # Most common — commit ran successfully (rk == 'done') OR the task
        # moved to a nested awaiting (then resolved). Either way, THIS card
        # is no longer the live one.
        return "confirmed"
    if task.status == TaskStatus.AWAITING_APPROVAL.value:
        # Awaiting on a DIFFERENT prepared_action_id (nested confirm
        # superseded this one). This card is stale.
        return "confirmed"
    return "unknown"


# ---------------------------------------------------------------------------
# /message handler
# ---------------------------------------------------------------------------

async def handle_message(
    session: Session, current_user: User, *,
    session_id: UUID, message_text: str,
    llm: Optional[LLMService] = None,
) -> AssistantEnvelope:
    """Persist user message, run splitter + runner, persist one assistant
    message describing the batch outcome. The session row is locked FOR
    UPDATE for the duration; concurrent /message calls against the same
    session wait.

    DELIBERATE NON-DEDUPE: the user message is persisted BEFORE the loop runs
    so a mid-call crash leaves a row with no assistant follow-up. On retry,
    the user message will appear twice in the transcript. That's the lesser
    evil — silent dedupe would drop legitimate "yes please do that again"
    follow-ups. The UI shows the user message immediately so the user has the
    signal to decide whether to resend; we never decide for them."""
    row = agent_chat_repo.get_session_for_update(
        session,
        session_id=session_id,
        business_id=current_user.business_id,
        user_id=current_user.id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="agent chat session not found")

    # ASK_USER RESOLUTION (Step 2 of the ask_user-lifecycle fix):
    # If this session has an originating ask_user task in needs_input
    # (status=awaiting_approval, pending_action_id NULL, result.kind
    # 'ask_user'), this incoming message is the answer to its question.
    # We need two things:
    #   1. The new batch's goal text must include the original question
    #      so the LLM has the context it needs (otherwise it sees "the
    #      first one" with no idea what was asked).
    #   2. After the new batch runs, mark the originating task DONE with
    #      result.kind='resolved_by_followup' so it leaves needs_input
    #      and doesn't grow the panel forever.
    # The task-based lookup is strictly more accurate than the prior
    # message-kind sniff (which missed multi-task batches entirely) and
    # locks the originating row for the duration of this transaction.
    originating_ask_user = agent_task_repo.latest_unresolved_ask_user_in_session(
        session,
        chat_session_id=row.id,
        business_id=current_user.business_id,
    )

    if originating_ask_user is not None:
        goal_for_loop = _augment_goal_from_ask_user_task(
            originating_ask_user, new_message=message_text,
        )
    else:
        # Fallback: legacy single-task ask_user path via message-kind
        # sniff. Kept for sessions whose ask_user task pre-dates this
        # fix or for any path that bypasses the task-side lookup. Plain
        # `message_text` when neither matches.
        goal_for_loop = _augment_goal_if_resuming_ask_user(
            session, row.id, new_message=message_text,
        )

    user_msg = agent_chat_repo.add_message(
        session, session_id=row.id, role="user", kind="text",
        content=message_text,
    )
    if not row.title:
        row.title = _derive_title(message_text)

    # Hand the augmented goal to the runner via a transient surrogate message
    # — the user_msg.content already has the literal text; the runner reads
    # content for splitting and the loop reads `goal_for_loop` indirectly
    # through the task description it creates. Simplest: stash the augmented
    # text by passing a copy of the user message with the augmented content
    # only for the runner's purposes.
    user_msg_for_runner = _surrogate_with_content(user_msg, goal_for_loop)

    batch = await run_message_as_tasks(
        session, current_user, row, user_msg_for_runner, llm=llm,
    )

    # Resolve the originating ask_user task once the new batch ran.
    # We do this AFTER the runner so the resolution payload can record
    # which new tasks took over the work (audit trail; the dashboard
    # filter excludes resolved_by_followup so the row disappears from
    # needs_input without resurfacing on recently_done).
    if originating_ask_user is not None:
        _mark_ask_user_resolved_by_followup(
            session, originating_ask_user, batch,
        )

    msg = _persist_batch_message(session, row, batch)
    session.commit()
    return _envelope_from_message(
        row, msg, batch_id=batch.batch_id, task_id=None,
    )


# ---------------------------------------------------------------------------
# /confirm handler
# ---------------------------------------------------------------------------

async def handle_confirm(
    session: Session, current_user: User, *,
    session_id: UUID, prepared_action_id: str,
    edits: Optional[dict[str, Any]] = None,
    llm: Optional[LLMService] = None,
) -> AssistantEnvelope:
    """Commit the prepared action, find its owning task, append a synth
    COMMITTED turn to THAT TASK's continuity_history, then resume run_agent
    on the task. The resumed run may end the task (done/error) or stage
    another prepare (nested confirm — allowed)."""
    # Session must exist + be tenant-owned, but we no longer gate on the
    # session-level awaiting_action_id. The task is the unit of pending state.
    row = agent_chat_repo.get_session_for_update(
        session,
        session_id=session_id,
        business_id=current_user.business_id,
        user_id=current_user.id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="agent chat session not found")

    task = agent_task_repo.get_by_pending_action_id(
        session,
        prepared_action_id=prepared_action_id,
        business_id=current_user.business_id,
    )
    if task is None or task.session_id != row.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("no task in this session is awaiting that prepared action — "
                    "it may have already been confirmed or cancelled"),
        )
    if task.status != TaskStatus.AWAITING_APPROVAL.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"task is not awaiting approval (status={task.status!r})",
        )

    # The user clicked "confirm", but the WRITE being committed was AI-prepared
    # — the resulting lead_activity should be labelled TASK (with the task /
    # session ids) rather than HUMAN (which the HTTP middleware set on the
    # outer request scope). Without this wrap, every AI-prepared write would
    # commit under HUMAN and the diary would lose the AI/TASK attribution.
    from app.core.actor_context import set_actor_context
    from app.models.enums import ActorType
    with set_actor_context(
        ActorType.TASK,
        chat_session_id=row.id,
        task_id=task.id,
    ):
        cr = confirm_prepared_action(
            session, current_user, action_id=prepared_action_id, edits=edits or {},
        )

    history = [turn_from_dict(d) for d in (task.continuity_history or [])]
    capability = _capability_from_last_prepare(history)

    if not cr.ok:
        # Commit failed: leave the task in awaiting_approval so the user can
        # retry confirm or cancel. Persist an error message describing the
        # failed commit; the multi_task bubble's slot for this task will be
        # refreshed by the frontend keying on task_id.
        safe_error = _safe_jsonify(cr.error or {})
        msg = agent_chat_repo.add_message(
            session,
            session_id=row.id, role="assistant", kind="error",
            content=str(safe_error.get("message") or "commit failed"),
            payload={"prepared_action_id": prepared_action_id,
                     "capability": capability,
                     "task_id": str(task.id),
                     "batch_id": str(task.batch_id),
                     "error": safe_error},
            turn_detail=None, tokens=None,
        )
        session.commit()
        return _envelope_from_message(
            row, msg, batch_id=task.batch_id, task_id=task.id,
        )

    # Commit landed. Append the synth COMMITTED turn to the TASK's history,
    # then re-invoke run_agent on the task.
    committed = synth_commit_record(
        history,
        prepared_action_id=prepared_action_id,
        capability=capability,
        cr=cr,
    )
    history.append(committed)
    task.continuity_history = [turn_to_dict(t) for t in history]
    agent_task_repo.save(session, task)

    resumed = await resume_task_after_commit(
        session, current_user, task, llm=llm,
    )

    # The post-resume assistant message describes the task's new state. Use
    # the existing per-kind shape so the frontend can drop the new state into
    # the right multi_task slot (or render as a fresh single message when the
    # batch was single-task to begin with).
    msg = _persist_task_update_message(
        session, row, resumed,
        first_committed_capability=capability,
        first_committed_action_id=prepared_action_id,
        first_committed_result=cr.result,
    )
    session.commit()
    return _envelope_from_message(
        row, msg, batch_id=resumed.batch_id, task_id=resumed.id,
    )


# ---------------------------------------------------------------------------
# /tasks/{task_id}/dismiss handler
# ---------------------------------------------------------------------------

def handle_dismiss_task(
    session: Session,
    current_user: User,
    *,
    session_id: UUID,
    task_id: UUID,
) -> AssistantEnvelope:
    """Owner explicitly dismisses a stuck task they no longer care
    about. Today this is the only escape hatch for ask_user tasks that
    the owner answered in a different surface (or just decided to
    forget); without it those rows live forever in needs_input.

    DELIBERATELY GENERIC endpoint name (/tasks/{id}/dismiss) — but the
    body is INTENTIONALLY NARROW: refuses to dismiss anything other
    than an ask_user task. Real awaiting-approval (a prepared write)
    has /confirm + /cancel as its proper resolution paths; pulling
    those out into a generic dismiss would let the owner silently
    abandon a prepared write without leaving the CANCELLED audit turn
    that /cancel appends. Defence in depth: the body re-checks the
    shape even though the endpoint name doesn't.

    Tenant scope: the parent session is fetched with
    business_id/user_id filters; the task lookup is then scoped to
    that session AND the user's business — same defence-in-depth as
    every other agent-chat handler."""
    row = agent_chat_repo.get_session_for_update(
        session,
        session_id=session_id,
        business_id=current_user.business_id,
        user_id=current_user.id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="agent chat session not found")

    task = agent_task_repo.get_for_update(
        session, task_id=task_id, business_id=current_user.business_id,
    )
    if task is None or task.session_id != row.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="task not found in this session",
        )

    # Shape check: only ask_user-style needs_input rows. Anything else
    # is refused with a 409 + explanation — generic dismiss should not
    # become a backdoor for skipping confirms.
    result = task.result or {}
    is_ask_user = (
        task.status == TaskStatus.AWAITING_APPROVAL.value
        and task.pending_action_id is None
        and isinstance(result, dict)
        and result.get("kind") == "ask_user"
    )
    if not is_ask_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "this task can't be dismissed — dismiss is only for tasks "
                "waiting on a clarifying question. For a prepared write, "
                "use /confirm or /cancel instead."
            ),
        )

    task.status = TaskStatus.DONE.value
    task.pending_action_id = None
    task.result = _safe_jsonify({
        "kind": "dismissed",
        "message": "user dismissed the ask_user question without answering",
    })
    agent_task_repo.save(session, task)

    msg = agent_chat_repo.add_message(
        session,
        session_id=row.id, role="assistant", kind="cancelled",
        content="Dismissed the assistant's question.",
        payload={"task_id": str(task.id),
                 "batch_id": str(task.batch_id),
                 "dismissed_question": (result.get("question") or "") if isinstance(result, dict) else ""},
        turn_detail=None,
        tokens=None,
    )
    session.commit()
    return _envelope_from_message(
        row, msg, batch_id=task.batch_id, task_id=task.id,
    )


# ---------------------------------------------------------------------------
# /cancel handler
# ---------------------------------------------------------------------------

def handle_cancel(
    session: Session, current_user: User, *,
    session_id: UUID, prepared_action_id: str,
) -> AssistantEnvelope:
    """User declined the awaiting prepare. We append a synthetic CANCELLED
    turn to the TASK's continuity_history (so any future resume would see
    the rejection) and mark the task DONE (cancelled). The loop is NOT
    re-invoked — the user said no; if they want a different approach they
    send a new /message (which becomes a new batch)."""
    row = agent_chat_repo.get_session_for_update(
        session,
        session_id=session_id,
        business_id=current_user.business_id,
        user_id=current_user.id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="agent chat session not found")

    task = agent_task_repo.get_by_pending_action_id(
        session,
        prepared_action_id=prepared_action_id,
        business_id=current_user.business_id,
    )
    if task is None or task.session_id != row.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no task in this session is awaiting that prepared action",
        )
    if task.status != TaskStatus.AWAITING_APPROVAL.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"task is not awaiting approval (status={task.status!r})",
        )

    history = [turn_from_dict(d) for d in (task.continuity_history or [])]
    capability = _capability_from_last_prepare(history)
    cancelled_turn = synth_cancel_record(
        history,
        prepared_action_id=prepared_action_id,
        capability=capability,
    )
    history.append(cancelled_turn)
    task.continuity_history = [turn_to_dict(t) for t in history]
    apply_cancel_to_task(task)
    agent_task_repo.save(session, task)

    msg = agent_chat_repo.add_message(
        session,
        session_id=row.id, role="assistant", kind="cancelled",
        content="Cancelled. Tell me what to change, or ask for something else.",
        payload={"prepared_action_id": prepared_action_id,
                 "capability": capability,
                 "task_id": str(task.id),
                 "batch_id": str(task.batch_id)},
        turn_detail=[turn_to_dict_no_raw(cancelled_turn)],
        tokens=None,
    )
    session.commit()
    return _envelope_from_message(
        row, msg, batch_id=task.batch_id, task_id=task.id,
    )


# ---------------------------------------------------------------------------
# Persistence helpers — batch + per-task update messages
# ---------------------------------------------------------------------------

def _persist_batch_message(
    session: Session, row: AgentChatSession, batch: TaskBatchResult,
) -> AgentChatMessage:
    """One assistant message per batch.

    Single-task batch — use the existing kinds so the frontend renders as
    today. Multi-task batch — use the new `multi_task` kind; payload carries
    a `tasks` array the renderer iterates over."""
    if len(batch.tasks) == 1:
        task = batch.tasks[0]
        kind, content, payload = _classify_task_result(task)
        # Add task_id + batch_id to payload so frontend can target this task
        # on a subsequent confirm/cancel update if needed (single-task batches
        # never actually NEED this, but uniformity helps).
        payload = {
            **payload,
            "task_id": str(task.id),
            "batch_id": str(batch.batch_id),
        }
        return agent_chat_repo.add_message(
            session, session_id=row.id, role="assistant", kind=kind,
            content=content, payload=payload,
            turn_detail=_turn_detail_for_task(task),
            tokens=task.tokens,
        )

    # Multi-task batch. Build per-task slots; the frontend renders one labeled
    # card per task in order. Plaintext fallback line is provided in `content`
    # for clients that don't understand `multi_task` yet — safety net only.
    slots = [_task_slot_payload(t) for t in batch.tasks]
    fallback = " | ".join(
        f"Task {t.sequence_index + 1} ({t.description!r}): "
        f"{_short_outcome_text(t)}"
        for t in batch.tasks
    )
    return agent_chat_repo.add_message(
        session, session_id=row.id, role="assistant", kind="multi_task",
        content=fallback,
        payload={
            "batch_id": str(batch.batch_id),
            "tasks": slots,
        },
        turn_detail=None,
        tokens=_sum_tokens_across_tasks(batch.tasks),
    )


def _persist_task_update_message(
    session: Session, row: AgentChatSession, task: AgentTask,
    *,
    first_committed_capability: str,
    first_committed_action_id: str,
    first_committed_result: Any,
) -> AgentChatMessage:
    """After /confirm resumes a task, persist a message describing the task's
    new state. The kind mirrors the task's terminal/new state so the frontend
    can drop it into the multi_task bubble's task slot (lookup by task_id),
    or, for single-task batches, render it as a regular follow-up message.

    Note: the FIRST commit (the one that just landed) is described by the
    `commit_result` payload fields. If the resumed loop staged a NESTED
    prepare, the new awaiting_confirm preview is what the frontend shows next;
    the just-landed commit is still recorded in payload['just_committed']."""
    just_committed = {
        "capability": first_committed_capability,
        "prepared_action_id": first_committed_action_id,
        "result": _safe_jsonify(first_committed_result),
    }

    kind, content, payload = _classify_task_result(task)
    # commit_result is the natural kind when the resumed loop went straight to
    # done after the commit; awaiting_confirm is nested confirm; error means
    # the resume itself failed. In all cases, we attach `just_committed` so
    # the UI knows the previous prepare actually landed.
    if task.status == TaskStatus.DONE.value and task.result and task.result.get("kind") == "done":
        # Promote to commit_result for backwards-compatible rendering.
        kind = "commit_result"
        content = f"Done — {first_committed_capability or 'write'} committed."

    payload = {
        **payload,
        "task_id": str(task.id),
        "batch_id": str(task.batch_id),
        "just_committed": just_committed,
    }

    return agent_chat_repo.add_message(
        session, session_id=row.id, role="assistant", kind=kind,
        content=content, payload=payload,
        turn_detail=_turn_detail_for_task(task),
        tokens=task.tokens,
    )


# ---------------------------------------------------------------------------
# Result -> (kind, content, payload) — per task
# ---------------------------------------------------------------------------

def _classify_task_result(task: AgentTask) -> tuple[str, Optional[str], dict[str, Any]]:
    """Map an AgentTask's terminal/awaiting state onto a (MessageKind, content,
    payload) triple. Mirrors the old _classify_result that operated directly
    on AgentRunResult — moved here because we now persist task rows in between.

    `task.result` was stored by the runner via _safe_jsonify, so all values
    are already JSON-safe."""
    result = task.result or {}
    rk = result.get("kind") or task.status

    if rk == "done":
        return "done", str(result.get("answer") or ""), {}

    if rk == "ask_user":
        q = result.get("question")
        return "ask_user", (str(q) if q else None), {"question": q}

    if rk == "awaiting_confirm":
        # Compound preamble carried in `content`; preview + ids in payload.
        return "awaiting_confirm", result.get("answer"), {
            "prepared_action_id": result.get("prepared_action_id"),
            "preview": result.get("preview"),
            "editable_fields": list(result.get("editable_fields") or []),
        }

    if rk == "cancelled":
        return "cancelled", str(result.get("message") or "Cancelled."), {}

    # error / exhausted / anything unexpected
    err = result.get("error") or {"code": rk, "message": str(rk)}
    msg = str(err.get("message") or rk)
    kind = rk if rk in ("error", "exhausted") else "error"
    return kind, msg, {"error": err}


def _task_slot_payload(task: AgentTask) -> dict[str, Any]:
    """The dict for one entry in a multi_task message's payload.tasks array.
    Each slot carries the task_id + description (so the renderer labels each
    card unambiguously) + the same fields the single-task kinds use. The
    `kind` field tells the renderer which sub-component to use."""
    sub_kind, content, sub_payload = _classify_task_result(task)
    return {
        "task_id": str(task.id),
        "sequence_index": task.sequence_index,
        "description": task.description,
        "status": task.status,
        "kind": sub_kind,
        "content": content,
        # Spread the per-kind sub-payload (prepared_action_id, preview, etc.)
        # at the top of the slot so the frontend doesn't need to look two
        # levels deep for the fields it already knows how to render.
        **sub_payload,
    }


def _short_outcome_text(task: AgentTask) -> str:
    """Per-task plaintext for the fallback `content` on multi_task messages.

    Was 120-char-capped per task. That capped a real user's complete answer
    mid-word ("…\n    **Es") when their frontend hadn't yet implemented the
    `multi_task` renderer and fell back to `content`. The fallback is
    supposed to be ugly-but-readable for old clients; truncating it
    violates the "readable" half. The full per-task payloads still live in
    `payload.tasks[]` for any client that does render multi_task properly."""
    result = task.result or {}
    rk = result.get("kind") or task.status
    if rk == "done":
        ans = str(result.get("answer") or "").strip()
        return ans or "done"
    if rk == "awaiting_confirm":
        return "awaiting your approval"
    if rk == "ask_user":
        return f"asking: {str(result.get('question') or '').strip()}"
    if rk == "cancelled":
        return "cancelled"
    err = result.get("error") or {}
    return f"failed: {str(err.get('message') or rk)}"


def _turn_detail_for_task(task: AgentTask) -> Optional[list[dict[str, Any]]]:
    """Build the per-message turn_detail snapshot (observation_raw stripped)
    from the task's continuity history. Returns None for tasks that haven't
    run yet — defensive; shouldn't happen for a persisted-batch message."""
    if not task.continuity_history:
        return None
    return [
        {
            "turn": d.get("turn"),
            "thought": d.get("thought"),
            "action": d.get("action"),
            "observation_summary": d.get("observation_summary"),
        }
        for d in task.continuity_history
    ]


def _sum_tokens_across_tasks(tasks: tuple[AgentTask, ...]) -> Optional[dict[str, Any]]:
    """Aggregate per-task TokenReport snapshots into a batch-level total.
    Shape matches `TokenReport.to_dict` keys the frontend already understands."""
    totals = {"turns": 0, "total_input_tokens": 0, "total_output_tokens": 0}
    per_turn: list[dict[str, Any]] = []
    for t in tasks:
        if not t.tokens:
            continue
        totals["turns"] += int(t.tokens.get("turns") or 0)
        totals["total_input_tokens"] += int(t.tokens.get("total_input_tokens") or 0)
        totals["total_output_tokens"] += int(t.tokens.get("total_output_tokens") or 0)
        per_turn.extend(list(t.tokens.get("per_turn") or []))
    return {**totals, "per_turn": per_turn} if per_turn else totals


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------

def _envelope_from_message(
    row: AgentChatSession, msg: AgentChatMessage,
    *,
    batch_id: Optional[UUID] = None,
    task_id: Optional[UUID] = None,
) -> AssistantEnvelope:
    return AssistantEnvelope(
        session_id=row.id,
        message_id=msg.id,
        kind=msg.kind,
        content=msg.content,
        payload=msg.payload or {},
        turn_detail=msg.turn_detail or [],
        tokens=msg.tokens,
        created_at=msg.created_at.isoformat(),
        awaiting_action_id=None,   # session-level gate retired; see module docstring
        batch_id=batch_id,
        task_id=task_id,
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _derive_title(first_message: str) -> str:
    """First line, trimmed to ~80 chars. Stored once per session; rename UI
    is out of scope for v1."""
    line = (first_message or "").strip().splitlines()[0] if first_message else ""
    return line[:80] if line else "Untitled chat"


def _capability_from_last_prepare(history: list) -> str:
    """Walk backwards for the most recent prepare action in this task's
    continuity history. The awaiting prepare is typically the last entry."""
    for t in reversed(history):
        if t.action.get("type") == "prepare":
            return str(t.action.get("capability") or "")
    return ""


def _surrogate_with_content(
    msg: AgentChatMessage, augmented_content: str,
) -> AgentChatMessage:
    """Return an in-memory copy of the AgentChatMessage with `content`
    replaced by the augmented goal text. Used to hand the augmented goal to
    the runner without overwriting the persisted user-message row (whose
    `content` must stay the literal text the user typed — that's what the UI
    renders in the user bubble).

    Important: we DON'T add this surrogate to the session — it's a transient
    object passed by value to the runner. The runner reads `.content` (for
    splitting and as the task description) and `.id` (for the task's
    user_message_id FK)."""
    if augmented_content == msg.content:
        return msg
    surrogate = AgentChatMessage(
        id=msg.id,
        session_id=msg.session_id,
        role=msg.role,
        kind=msg.kind,
        content=augmented_content,
        payload=msg.payload,
        turn_detail=msg.turn_detail,
        tokens=msg.tokens,
        created_at=msg.created_at,
    )
    return surrogate


def _augment_goal_from_ask_user_task(
    originating: "AgentTask",
    *,
    new_message: str,
) -> str:
    """Build the augmented goal text from an originating ask_user TASK
    (not from a chat message). Mirrors the shape of
    `_augment_goal_if_resuming_ask_user` so the LLM sees the same
    `[Earlier I asked you to clarify: ...]` / `[You replied: ...]`
    markers — and so cross_message_memory's marker-sniff precedence
    still works (an augmented goal must NOT also get the prior-batch
    preamble stacked on top).

    The original question lives on the task row at
    `task.result['question']` (set by the runner's
    `_apply_result_to_task`). The 'prior goal' is the task's own
    description — that was the message the assistant was asking about."""
    from app.agent.cross_message_memory import ASK_USER_AUGMENTATION_MARKER
    # Same drift-guard as the message-based helper.
    assert ASK_USER_AUGMENTATION_MARKER == "[Earlier I asked you to clarify:", (
        "augmentation marker drifted out of sync with cross_message_memory.py — "
        "update both call sites together"
    )

    result = originating.result or {}
    question = ""
    if isinstance(result, dict):
        question = str(result.get("question") or "")
    prior_goal = (originating.description or "").strip()

    parts: list[str] = []
    if prior_goal:
        parts.append(prior_goal)
    parts.append(f"[Earlier I asked you to clarify: {question!r}]")
    parts.append(f"[You replied: {new_message}]")
    return "\n\n".join(parts)


def _mark_ask_user_resolved_by_followup(
    session: Session,
    originating: "AgentTask",
    batch: "TaskBatchResult",
) -> None:
    """Move the originating ask_user task from needs_input to a
    terminal state. The new batch did the work; the original task is
    flagged 'resolved_by_followup' with a payload pointing at the
    batch that resolved it — useful for audit / debugging.

    Status moves to DONE so the dashboard's status-based bucket queries
    stop returning it. The notable filter on the recently-done bucket
    excludes result.kind='resolved_by_followup' (see dashboard_service)
    so it doesn't resurface there either — these tasks aren't actually
    work, they're just lifecycle markers."""
    from app.models.agent_task import TaskStatus
    originating.status = TaskStatus.DONE.value
    originating.pending_action_id = None    # was already NULL; defensive
    originating.result = _safe_jsonify({
        "kind": "resolved_by_followup",
        "resolved_by_batch_id": str(batch.batch_id),
        "resolved_by_task_ids": [str(t.id) for t in batch.tasks],
    })
    agent_task_repo.save(session, originating)


def _augment_goal_if_resuming_ask_user(
    session: Session, session_id: UUID, *, new_message: str,
) -> str:
    """If the IMMEDIATE prior assistant message in this session was a
    single-task `ask_user`, rebuild the goal so the resumed loop sees the
    original intent + the clarifying question + the user's reply.

    LIMITATIONS for v1:
      * Only fires when the prior message kind is literally `ask_user`. A
        `multi_task` message that contained an ask_user slot does NOT trigger
        this — multi-task ask_user is a known v1 limitation (the user can't
        easily resume just one task in a batch).
      * Only augments for the IMMEDIATE prior ask_user. A chained sequence
        (ask -> reply -> ask -> reply) only carries the most recent question
        into the next run."""
    msgs = agent_chat_repo.list_messages(session, session_id=session_id)
    last_assistant = next((m for m in reversed(msgs) if m.role == "assistant"), None)
    if last_assistant is None or last_assistant.kind != "ask_user":
        return new_message

    question = (
        (last_assistant.payload or {}).get("question")
        or last_assistant.content
        or ""
    )

    try:
        ask_idx = msgs.index(last_assistant)
    except ValueError:
        ask_idx = len(msgs)
    prior_goal = next(
        (m.content for m in reversed(msgs[:ask_idx])
         if m.role == "user" and m.content),
        None,
    )

    # PRECEDENCE-CRITICAL: this exact marker prefix is what the multi-task
    # runner sniffs (via cross_message_memory.is_already_augmented) to decide
    # whether to ALSO prepend the prior-batch preamble. If they double-stack,
    # the LLM sees two flavours of "previous context" and gets confused.
    # The marker string is centralised in cross_message_memory.py as
    # ASK_USER_AUGMENTATION_MARKER — if you ever rename it, change BOTH or
    # the precedence silently breaks. (Marker-sniffing is the v1 expedient;
    # the proper fix if precedence bugs surface is an explicit
    # skip_preamble flag plumbed through the call chain.)
    from app.agent.cross_message_memory import ASK_USER_AUGMENTATION_MARKER
    assert ASK_USER_AUGMENTATION_MARKER == "[Earlier I asked you to clarify:", (
        "augmentation marker drifted out of sync with cross_message_memory.py — "
        "update both call sites together"
    )

    parts: list[str] = []
    if prior_goal:
        parts.append(prior_goal)
    parts.append(f"[Earlier I asked you to clarify: {question!r}]")
    parts.append(f"[You replied: {new_message}]")
    return "\n\n".join(parts)
