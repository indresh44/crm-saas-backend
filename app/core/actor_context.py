"""Per-request ambient actor context.

Solves: every lead-activity emit site needs to record WHO is acting (the
owner via the UI, an AI capability, a multi-task batch, a system webhook),
plus optionally which chat session and which task. Threading these
through every service method and every capability `execute()` signature
would touch dozens of files and is easy to silently miss; the cost of
missing one is a misattributed activity (an AI write logged as HUMAN
would defeat the purpose of the actor field).

Solution: a `contextvars.ContextVar` carrying an `ActorContext`. Set once
per request scope (FastAPI dependency / multi-task runner / agent loop /
webhook handler) and read at the chokepoint where every activity row is
born (`lead_repository.create_lead_activity`). Existing service signatures
do not change.

Async correctness: `asyncio` propagates contextvars across `await` and
across `asyncio.gather` children. If a future code path spawns a bare
thread without `contextvars.copy_context()`, the actor would default to
SYSTEM and a warning would log — bugs are loud, not silent.

Default actor: SYSTEM. The default is intentionally the "less informative
but plausible" choice — a missed context-set produces a log warning, not
a mislabeled HUMAN/AI entry. Tests / scripts / management commands that
write activities outside a request scope are correctly labeled SYSTEM.
"""

from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional
from uuid import UUID

from app.models.enums import ActorType


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActorContext:
    actor_type: ActorType
    chat_session_id: Optional[UUID] = None
    task_id: Optional[UUID] = None


_DEFAULT_CONTEXT = ActorContext(actor_type=ActorType.SYSTEM)


# ContextVar holds the ambient actor for the current async task / thread.
# Default is SYSTEM so out-of-request writes (scripts, tests that don't set
# context, ad-hoc shell sessions) get a stable, honest label.
_current_actor: contextvars.ContextVar[ActorContext] = contextvars.ContextVar(
    "lead_activity_actor", default=_DEFAULT_CONTEXT,
)


def current_actor() -> ActorContext:
    """Return the ambient actor context for the current scope. Falls back to
    SYSTEM if no scope has set one — see module docstring."""
    return _current_actor.get()


@contextmanager
def set_actor_context(
    actor_type: ActorType,
    *,
    chat_session_id: Optional[UUID] = None,
    task_id: Optional[UUID] = None,
    prefer_existing: bool = False,
) -> Iterator[ActorContext]:
    """Bind an ActorContext for the duration of a `with` block.

    `prefer_existing` — when True, if a more specific context is already
    bound (specifically: an inner caller wants to set AI but the outer
    scope has already set TASK with a task_id), the existing context wins.
    This is the precedence rule between the agent loop (AI) and the
    multi-task runner (TASK): when the runner is in play, the loop's
    inner wrapper should not overwrite it. See the comments in
    `multi_task_runner.py` and `loop.py` capability dispatch."""
    existing = _current_actor.get()
    if prefer_existing and _is_more_specific(existing, actor_type):
        # Outer context is more specific; don't overwrite. Yielding the
        # existing context (not a new one) so the caller sees what it
        # actually got.
        yield existing
        return

    new_ctx = ActorContext(
        actor_type=actor_type,
        chat_session_id=chat_session_id,
        task_id=task_id,
    )
    token = _current_actor.set(new_ctx)
    try:
        yield new_ctx
    finally:
        _current_actor.reset(token)


def _is_more_specific(existing: ActorContext, proposed: ActorType) -> bool:
    """Specificity ordering for the prefer_existing rule.

    TASK beats AI (TASK is "AI in a multi-task batch" — strictly more
    informative). Everything else: the proposed context wins. This is
    intentionally narrow — we do NOT want a stale leftover HUMAN/SYSTEM
    context to suppress a legitimate inner re-set."""
    if existing.actor_type is ActorType.TASK and proposed is ActorType.AI:
        return True
    return False


def warn_if_default_on_write() -> None:
    """Called from the repository chokepoint just before stamping a row.
    If the ambient context is the unset default (SYSTEM with no ids and
    no caller-bound override), emit a warning — a write reaching the
    chokepoint without an explicit actor is usually a bug (an HTTP route
    or runner that forgot to set context). Tests/scripts that legitimately
    write as SYSTEM should set context explicitly so this stays silent."""
    ctx = _current_actor.get()
    if (
        ctx is _DEFAULT_CONTEXT
        or (
            ctx.actor_type is ActorType.SYSTEM
            and ctx.chat_session_id is None
            and ctx.task_id is None
            # The default object identity check above catches the unset
            # case; a SYSTEM context set explicitly (e.g. webhook) will
            # not be `is _DEFAULT_CONTEXT` and so won't trigger here.
            and ctx is _DEFAULT_CONTEXT
        )
    ):
        logger.warning(
            "lead_activity write reached the repository chokepoint without "
            "an explicit actor context — defaulting to SYSTEM. If this is "
            "an HTTP request or agent invocation, the wiring is wrong.",
        )
