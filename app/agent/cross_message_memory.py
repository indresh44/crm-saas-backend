"""Cross-message memory — Stage-2 in-session continuity.

A new /message in an existing session is no longer amnesiac: before its tasks
run, the runner prepends a PRIOR MESSAGE CONTEXT preamble to each task's
goal text. The preamble is built fresh from the IMMEDIATELY PRIOR batch's
COMPLETED tasks — summarised, never raw.

What's deliberately NOT done:
  * No UUID is threaded across messages. The loop's UUID-provenance rail
    (which rejects any prepare whose UUID inputs weren't seen in THIS call's
    reads) stays intact. Carried names/numbers are information; if the new
    task needs an id, it must re-read.
  * Only the immediately prior batch is carried. Messages older than that are
    NOT in the preamble. Token growth is bounded.
  * Awaiting tasks from the prior batch are skipped — they have no resolved
    answer to reference.
  * Within-task continuity (resume after confirm/ask_user) is unchanged. This
    module operates between messages, not within a task.

TENANT SCOPE — HARD REQUIREMENT (not a defence-in-depth):
  `build_prior_context_preamble` MUST filter on business_id, not just
  session_id. The preamble is injected directly into the LLM context, so a
  cross-tenant leak here is high-severity and silent — it cannot be caught
  downstream. Every caller passes business_id explicitly; every test asserts
  the cross-tenant boundary holds.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.agent_task import AgentTask, TaskStatus


# ---------------------------------------------------------------------------
# Caps. Per-task summaries are bounded so a verbose prior batch can't blow
# the preamble token budget. Numbers chosen to fit "the last batch carried as
# context" inside ~700 input tokens worst case (4 tasks × ~700 chars).
# ---------------------------------------------------------------------------

_ANSWER_CAP = 400
_LAST_READ_SUMMARY_CAP = 300


# ---------------------------------------------------------------------------
# Marker strings — shared with agent_chat_service for the precedence check.
#
# The runner inspects `goal` for these markers to decide whether the chat
# service already wrote an ask_user augmentation. If yes, the runner SKIPS
# the prior-batch preamble (the augmentation is more targeted and the two
# would double-stack).
#
# Note: marker-string sniffing is the v1 expedient. If precedence bugs surface
# in practice, the proper fix is an explicit `skip_preamble: bool` flag passed
# through the call chain — NOT cleverer regex. Marker-sniffing is fragile and
# every additional sniff site multiplies that fragility.
# ---------------------------------------------------------------------------

ASK_USER_AUGMENTATION_MARKER = "[Earlier I asked you to clarify:"

PREAMBLE_HEADER = "[PRIOR MESSAGE — already completed; do NOT redo]"
PREAMBLE_FOOTER = "— end of prior context —"
NEW_REQUEST_LEAD_IN = "Now the user has just said:"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_prior_context_preamble(
    session: Session,
    *,
    chat_session_id: UUID,
    business_id: UUID,
    exclude_batch_id: Optional[UUID] = None,
) -> Optional[str]:
    """Return a rendered preamble string, or None if there's no prior batch.

    `business_id` is REQUIRED and is applied as a hard filter on the underlying
    tasks query — there is no overload that lets a caller skip it. A foreign
    business cannot see another tenant's prior batch even if a chat_session_id
    is somehow spoofed.

    `exclude_batch_id` excludes the current batch in flight (when the runner
    has already inserted the new batch's rows before calling this — the
    runner's current flow inserts AFTER, so this is usually None, but the
    arg exists so a future caller doesn't have to re-order their code)."""
    prior_tasks = _load_prior_batch_tasks(
        session,
        chat_session_id=chat_session_id,
        business_id=business_id,
        exclude_batch_id=exclude_batch_id,
    )
    if not prior_tasks:
        return None

    cards = [_summarise_task(t) for t in prior_tasks if _is_terminal(t)]
    if not cards:
        # Prior batch existed but every task was awaiting/queued/running.
        # Nothing useful to carry — skip the preamble entirely rather than
        # rendering an empty "you previously completed: (nothing)" block.
        return None

    return _render_preamble(cards)


def is_already_augmented(goal: str) -> bool:
    """True when the goal already carries the chat service's ask_user
    augmentation marker. The runner uses this to skip the prior-batch
    preamble when the more-targeted ask_user augmentation is in play."""
    return ASK_USER_AUGMENTATION_MARKER in (goal or "")


def prepend_preamble(preamble: Optional[str], goal: str) -> str:
    """Final concatenation step — kept as a helper so the join shape is
    consistent across runner / tests. Returns `goal` unchanged when there's
    no preamble. Also returns `goal` unchanged when the goal is already
    augmented (precedence: ask_user augmentation wins; see module docstring)."""
    if not preamble:
        return goal
    if is_already_augmented(goal):
        return goal
    return f"{preamble}\n\n{NEW_REQUEST_LEAD_IN}\n{goal}"


# ---------------------------------------------------------------------------
# Internals — pure functions, easy to test without a DB
# ---------------------------------------------------------------------------

def _load_prior_batch_tasks(
    session: Session,
    *,
    chat_session_id: UUID,
    business_id: UUID,
    exclude_batch_id: Optional[UUID],
) -> list[AgentTask]:
    """Find the most-recent batch in this session (excluding `exclude_batch_id`
    if given) and return all of its tasks in sequence_index order. Returns
    [] when the session has no prior batch.

    HARD TENANT FILTER: both the latest-task probe and the sibling fetch
    filter on business_id. The probe is the riskier one — without that
    filter, a session_id collision (admittedly unlikely with UUIDs) could
    surface a foreign-tenant batch."""
    # Probe: latest task in this session that isn't in the excluded batch.
    probe_stmt = (
        select(AgentTask)
        .where(
            AgentTask.session_id == chat_session_id,
            AgentTask.business_id == business_id,
        )
        .order_by(AgentTask.created_at.desc(), AgentTask.id.desc())
    )
    if exclude_batch_id is not None:
        probe_stmt = probe_stmt.where(AgentTask.batch_id != exclude_batch_id)
    latest = session.exec(probe_stmt.limit(1)).first()
    if latest is None:
        return []

    # Siblings (the rest of latest.batch_id), tenant-filtered.
    siblings_stmt = (
        select(AgentTask)
        .where(
            AgentTask.session_id == chat_session_id,
            AgentTask.business_id == business_id,
            AgentTask.batch_id == latest.batch_id,
        )
        .order_by(AgentTask.sequence_index.asc())
    )
    return list(session.exec(siblings_stmt).all())


def _is_terminal(task: AgentTask) -> bool:
    """A task contributes to the preamble only if it reached a terminal state
    with a resolved outcome. queued/running/awaiting_approval contribute
    nothing — there's no answer yet."""
    return task.status in (
        TaskStatus.DONE.value,
        TaskStatus.FAILED.value,
    )


def _summarise_task(task: AgentTask) -> dict[str, Any]:
    """Build the per-task memory card. Pure function — takes the persisted
    task row, returns a dict the renderer turns into prompt text. Defensive
    against missing/malformed result dicts (the runner _safe_jsonify's them
    on write, but a partial-batch crash could leave incomplete shapes)."""
    result = task.result or {}
    rk = result.get("kind") or task.status
    status_label = _status_label(task.status, rk)

    answer = _extract_answer_text(result, rk)
    last_read = _extract_last_read_summary(task.continuity_history or [])

    return {
        "description": task.description,
        "status_label": status_label,
        "answer": _truncate(answer, _ANSWER_CAP),
        "last_read": _truncate(last_read, _LAST_READ_SUMMARY_CAP),
    }


def _status_label(task_status: str, result_kind: str) -> str:
    """Human-readable status that the LLM will see. For DONE-by-cancel we
    want the LLM to know the user declined, not that the prepare landed."""
    if result_kind == "cancelled":
        return "cancelled by user"
    if task_status == TaskStatus.FAILED.value:
        return "failed"
    if task_status == TaskStatus.DONE.value:
        return "done"
    return task_status


def _extract_answer_text(result: dict[str, Any], rk: str) -> Optional[str]:
    """Pull a one-line answer from the task result. Each kind stores its
    user-facing text in a different field — handled explicitly so a typo in
    the runner's mapping doesn't silently produce empty cards."""
    if rk == "done":
        v = result.get("answer")
        return str(v) if v is not None else None
    if rk == "cancelled":
        v = result.get("message")
        return str(v) if v is not None else None
    if rk in ("error", "exhausted") or "error" in result:
        err = result.get("error") or {}
        v = err.get("message") or err.get("code")
        return str(v) if v is not None else None
    return None


def _extract_last_read_summary(continuity_history: list[dict]) -> Optional[str]:
    """Find the observation_summary of the most-recent successful READ turn.
    That field already contains the entity references (names, numbers) the
    next message most often wants to reference — it's the gold of the
    preamble. Walk backwards so the LATEST read wins when there were many."""
    for turn in reversed(continuity_history):
        action = turn.get("action") or {}
        if action.get("type") != "read":
            continue
        # Skip reads that produced errors — their summary is "read failed:"
        # which is the wrong context to carry forward.
        raw = turn.get("observation_raw") or {}
        if raw.get("kind") == "error":
            continue
        summary = turn.get("observation_summary")
        if summary:
            return str(summary)
    return None


def _truncate(text: Optional[str], cap: int) -> Optional[str]:
    if text is None:
        return None
    t = text.strip()
    if not t:
        return None
    if len(t) <= cap:
        return t
    # Trim mid-word is fine — this is LLM context, not a UI label. The cap
    # is generous enough that a trimmed read summary still names enough
    # entities to be useful.
    return t[: cap - 1] + "…"


def _render_preamble(cards: list[dict[str, Any]]) -> str:
    """Render the carded prior-batch summary as the preamble text. The
    delimiters and lead-in line ('do NOT redo', 'Resolve by name; re-READ
    if you need IDs', 'Now the user has just said:') are prompt-engineering
    rails — see risks #1 and #2 in the build plan."""
    lines: list[str] = [
        PREAMBLE_HEADER,
        "The user's previous message in this chat produced these results.",
        "Treat this as background context — the user's NEW request may",
        "reference items from below. Resolve references by name or number;",
        "re-READ if you need IDs.",
        "",
    ]
    for card in cards:
        lines.append(f"Task: {card['description']!r}")
        lines.append(f"  Status: {card['status_label']}")
        if card.get("answer"):
            lines.append(f"  Result: {card['answer']}")
        if card.get("last_read"):
            # Indent the read summary's own newlines for readability in
            # the prompt; the read-summary template is multi-line.
            indented = card["last_read"].replace("\n", "\n    ")
            lines.append(f"  Last read: {indented}")
        lines.append("")
    lines.append(PREAMBLE_FOOTER)
    return "\n".join(lines)
