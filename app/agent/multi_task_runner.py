"""Multi-task runner — orchestrates split tasks above `run_agent`.

Flow:
  1. Splitter turns one owner message into >=1 task descriptions.
  2. Create one AgentTask row per description (status='queued', shared
     batch_id, monotonic sequence_index).
  3. Walk tasks in sequence_index order. For each task:
       - lock the task row, set status='running'
       - call run_agent(goal=description, prior_history=task.continuity_history)
       - map the result back to task status / result / pending_action_id
       - persist updated continuity_history + tokens
       - one task failing does NOT abort the others — continue the loop
  4. Return the batch outcome (batch_id + ordered task rows).

The runner does NOT block on awaiting_approval. Each task records its pause
(status='awaiting_approval', pending_action_id set) and the next task still
runs. The chat surface renders all task slots in one assistant message;
/confirm and /cancel target a specific task by prepared_action_id.

The session-level lock the chat service holds (the `get_session_for_update`
on AgentChatSession) is what serialises an entire /message call against
concurrent /confirms; this runner does NOT take session-level locks of its
own. Per-task locking happens inside the loop so a future parallel runner can
adopt this same shape unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Session

from app.agent.cross_message_memory import (
    build_prior_context_preamble,
    prepend_preamble,
)
from app.agent.loop import AgentRunResult, run_agent
from app.agent.serialize import _safe_jsonify, turn_from_dict, turn_to_dict
from app.agent.task_splitter import split_into_tasks
from app.core.actor_context import set_actor_context
from app.models.agent_chat import AgentChatMessage, AgentChatSession
from app.models.agent_task import AgentTask, TaskStatus
from app.models.enums import ActorType
from app.models.user import User
from app.repositories import agent_task_repo
from app.services.llm_service import LLMService


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskBatchResult:
    """The runner's output: the batch id + the post-run task rows, in split
    order. The chat service composes one assistant message per batch from
    this shape."""
    batch_id: UUID
    tasks: tuple[AgentTask, ...]


async def run_message_as_tasks(
    session: Session,
    current_user: User,
    chat_session: AgentChatSession,
    user_message: AgentChatMessage,
    *,
    llm: Optional[LLMService] = None,
) -> TaskBatchResult:
    """Split the owner's message, create task rows, run them sequentially,
    return the batch outcome.

    The caller MUST already hold the session-level row lock (chat service
    pattern); this function only commits intermediate writes per task so a
    crash mid-batch leaves partial-but-consistent state (some tasks done,
    others queued). The runner DOES NOT raise — exceptions inside individual
    tasks are caught and recorded as failed, so one bad task can't abort the
    batch."""
    batch_id = uuid4()
    descriptions = await split_into_tasks(user_message.content or "", llm=llm)

    # CROSS-MESSAGE MEMORY (Stage-2 in-session continuity):
    # Compute the prior-batch preamble BEFORE inserting the new batch's tasks
    # so the lookup can't ever find our own freshly-inserted rows. The helper
    # filters on business_id as a HARD requirement — see the module docstring
    # in cross_message_memory.py. None when there's no prior batch (first
    # message in a session) or every prior task was non-terminal.
    prior_preamble = build_prior_context_preamble(
        session,
        chat_session_id=chat_session.id,
        business_id=current_user.business_id,
    )

    # Create rows up front so the whole batch is visible even if we crash
    # mid-loop. Single-task batches go through the same path — uniform shape
    # makes the chat-service rendering branch-free.
    tasks: list[AgentTask] = []
    for i, desc in enumerate(descriptions):
        row = agent_task_repo.create_task(
            session,
            business_id=current_user.business_id,
            session_id=chat_session.id,
            user_message_id=user_message.id,
            batch_id=batch_id,
            sequence_index=i,
            description=desc,
        )
        tasks.append(row)
    session.flush()

    # Walk the batch sequentially. The loop is `async` because `run_agent` is.
    # `prior_preamble` is constant for the batch — every task in this batch
    # sees the same prior-message context.
    for task in tasks:
        await _run_one_task(
            session, current_user, task, llm=llm,
            prior_preamble=prior_preamble,
        )

    # Re-fetch in split order so callers see a clean ordered tuple regardless
    # of what the ORM session has cached.
    refreshed = agent_task_repo.list_by_batch(
        session, session_id=chat_session.id, batch_id=batch_id,
    )
    return TaskBatchResult(batch_id=batch_id, tasks=tuple(refreshed))


async def _run_one_task(
    session: Session,
    current_user: User,
    task: AgentTask,
    *,
    llm: Optional[LLMService] = None,
    prior_preamble: Optional[str] = None,
) -> None:
    """Run a single task end-to-end (one run_agent call). On any unexpected
    exception, marks the task failed and returns — never re-raises, so a
    crash on task N does NOT abort tasks N+1..M.

    `prior_preamble` is the cross-message memory preamble (built once per
    batch in `run_message_as_tasks`). It is prepended to the goal text PASSED
    TO run_agent — it is NOT persisted on the task row. `task.description`
    remains the user-facing original. None when there's nothing to carry."""
    # Lock the task row + mark running. Done in a tight scope so the lock is
    # released as soon as we've recorded the start.
    locked = agent_task_repo.get_for_update(
        session, task_id=task.id, business_id=current_user.business_id,
    )
    if locked is None:
        # Should be impossible — we just created it in the same transaction.
        logger.error("task %s vanished between create and run", task.id)
        return
    locked.status = TaskStatus.RUNNING.value
    agent_task_repo.save(session, locked)

    prior = tuple(turn_from_dict(d) for d in (locked.continuity_history or []))

    # Cross-message memory: prepend the prior-batch preamble to the goal text
    # the loop sees. If the goal already carries the chat service's ask_user
    # augmentation marker, `prepend_preamble` returns it unchanged — that
    # augmentation is more targeted and takes precedence (see module
    # docstring in cross_message_memory.py).
    goal_for_loop = prepend_preamble(prior_preamble, locked.description)

    # Bind the diary actor for the duration of this task. Any LeadActivity
    # row created downstream (via a write capability's service call) inherits
    # ActorType.TASK + this batch's session/task ids automatically — no
    # service signature has to thread these. See app/core/actor_context.py.
    try:
        with set_actor_context(
            ActorType.TASK,
            chat_session_id=locked.session_id,
            task_id=locked.id,
        ):
            result = await run_agent(
                session, current_user,
                goal=goal_for_loop,
                prior_history=prior,
                llm=llm,
            )
    except Exception as exc:  # noqa: BLE001 — runner-level isolation guarantee
        # Hard isolation: anything that escapes run_agent becomes a failed
        # task with a generic error payload. The batch continues.
        logger.exception("task %s crashed inside run_agent", locked.id)
        locked.status = TaskStatus.FAILED.value
        locked.result = _safe_jsonify({
            "kind": "error",
            "error": {
                "code": "runner_exception",
                "message": f"{type(exc).__name__}: {exc}",
            },
        })
        agent_task_repo.save(session, locked)
        return

    _apply_result_to_task(locked, result)
    agent_task_repo.save(session, locked)


def _apply_result_to_task(task: AgentTask, result: AgentRunResult) -> None:
    """Map AgentRunResult onto the task row in place. The caller persists.

    Keeps every branch funnelled through `_safe_jsonify` because the result
    dicts may carry Decimals/UUIDs from capability preview computations."""
    # Always overwrite continuity + tokens — these reflect the LATEST run_agent
    # call (which may be a resume after confirm).
    task.continuity_history = [turn_to_dict(t) for t in result.history]
    task.tokens = _safe_jsonify(result.tokens.to_dict())

    if result.kind == "done":
        task.status = TaskStatus.DONE.value
        task.pending_action_id = None
        task.result = _safe_jsonify({
            "kind": "done",
            "answer": result.answer,
        })
        return

    if result.kind == "awaiting_confirm":
        task.status = TaskStatus.AWAITING_APPROVAL.value
        task.pending_action_id = result.prepared_action_id
        task.result = _safe_jsonify({
            "kind": "awaiting_confirm",
            "prepared_action_id": result.prepared_action_id,
            "preview": result.preview,
            "editable_fields": list(result.editable_fields),
            # Optional compound preamble (loop's `answer` on a prepare turn).
            "answer": result.answer,
        })
        return

    if result.kind == "ask_user":
        # ask_user pauses the task awaiting a textual reply, not a confirm.
        # Modelled as `awaiting_approval` because the lifecycle column has no
        # finer state; pending_action_id stays NULL so /confirm can't target
        # it. The next /message on the session resumes via the existing
        # ask_user augmentation path in the chat service.
        task.status = TaskStatus.AWAITING_APPROVAL.value
        task.pending_action_id = None
        task.result = _safe_jsonify({
            "kind": "ask_user",
            "question": result.question,
        })
        return

    # error or exhausted -> failed
    task.status = TaskStatus.FAILED.value
    task.pending_action_id = None
    task.result = _safe_jsonify({
        "kind": result.kind,
        "error": result.error or {"code": result.kind, "message": result.kind},
    })


async def resume_task_after_commit(
    session: Session,
    current_user: User,
    task: AgentTask,
    *,
    llm: Optional[LLMService] = None,
) -> AgentTask:
    """After a successful /confirm has committed the prepared action AND the
    caller has appended a synthetic COMMITTED turn to task.continuity_history,
    re-invoke run_agent on this task. The result may finish the task, stage
    another prepare (nested confirm — allowed), or fail.

    Caller is responsible for the synth-commit append; this function only runs
    the loop and applies the result back to the task. The task row is assumed
    LOCKED by the caller for the duration."""
    task.status = TaskStatus.RUNNING.value
    task.pending_action_id = None
    agent_task_repo.save(session, task)

    prior = tuple(turn_from_dict(d) for d in (task.continuity_history or []))
    try:
        # Resume runs under the same TASK actor as the initial dispatch —
        # otherwise an activity emitted by the resumed loop (e.g. a post-
        # commit follow-up read that triggers a new activity) would be
        # labelled HUMAN (the outer HTTP middleware), which is wrong.
        with set_actor_context(
            ActorType.TASK,
            chat_session_id=task.session_id,
            task_id=task.id,
        ):
            result = await run_agent(
                session, current_user,
                goal=task.description,
                prior_history=prior,
                llm=llm,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("task %s crashed on resume", task.id)
        task.status = TaskStatus.FAILED.value
        task.result = _safe_jsonify({
            "kind": "error",
            "error": {
                "code": "resume_exception",
                "message": f"{type(exc).__name__}: {exc}",
            },
        })
        agent_task_repo.save(session, task)
        return task

    _apply_result_to_task(task, result)
    agent_task_repo.save(session, task)
    return task


def apply_cancel_to_task(task: AgentTask) -> None:
    """Mark a task DONE after the user cancelled its pending prepare. We don't
    re-invoke run_agent — the user said no and we surface that as a terminal
    state for this task. If they want the assistant to try a different
    approach, that's a fresh /message (which becomes a new batch).

    Continuity_history is left intact; the synth CANCELLED record the caller
    appended remains in the history for audit. We do NOT resume the loop here
    because resuming would tend to re-emit a near-identical prepare (the
    CANCELLED record only weakly nudges the LLM away)."""
    task.status = TaskStatus.DONE.value
    task.pending_action_id = None
    task.result = _safe_jsonify({
        "kind": "cancelled",
        "message": "user declined the prepared action",
    })
