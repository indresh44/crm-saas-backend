"""TurnRecord <-> JSON, the post-commit COMMITTED synth, the post-cancel
CANCELLED synth, and the _safe_jsonify boundary helper.

Single source of truth used by BOTH the ask.py terminal harness and the new
agent_chat_service. Keeping them in lockstep is what guarantees the LLM sees
the same continuation signals regardless of which surface drove the loop.

JSON-SAFETY (required, not optional):
    Capability execute() hooks may return dicts containing Decimal, datetime,
    UUID, etc. Those values are JSON-safe via str() conversion but NOT via
    `json.dumps` without `default=`. `_safe_jsonify` is applied at the service
    boundary to every commit result and to every observation_raw we synthesize.
    This is non-negotiable: a Decimal in continuity_history would crash the
    JSONB write and leave the session row in an inconsistent state.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.agent.confirm import ConfirmResult
from app.agent.observations import TurnRecord


# ---------------------------------------------------------------------------
# JSON-safety boundary
# ---------------------------------------------------------------------------

# The implementation moved to `app/core/json_safe.py` so business services
# (invoice, lead, payment) can use it without importing from `app/agent/`
# (wrong layer direction). This re-export preserves the existing
# `from app.agent.serialize import _safe_jsonify` imports across the agent
# subtree.
from app.core.json_safe import safe_jsonify as _safe_jsonify  # noqa: F401


# ---------------------------------------------------------------------------
# TurnRecord <-> JSON
# ---------------------------------------------------------------------------

def turn_to_dict(t: TurnRecord) -> dict[str, Any]:
    """Full serialization — keeps observation_raw. Used for persisting
    continuity_history on the session row (the LLM never sees raw, but the
    UUID-provenance check inside run_agent needs it on the next call)."""
    return {
        "turn": t.turn,
        "thought": t.thought,
        "action": _safe_jsonify(t.action),
        "observation_summary": t.observation_summary,
        "observation_raw": _safe_jsonify(t.observation_raw),
    }


def turn_to_dict_no_raw(t: TurnRecord) -> dict[str, Any]:
    """Display-safe serialization — drops observation_raw. Used for the
    per-message turn_detail field that the chat client renders in its debug
    panel; raw stays server-side only."""
    return {
        "turn": t.turn,
        "thought": t.thought,
        "action": _safe_jsonify(t.action),
        "observation_summary": t.observation_summary,
    }


def turn_from_dict(d: dict[str, Any]) -> TurnRecord:
    """Inverse of `turn_to_dict`. Tolerates a missing observation_raw (e.g.
    legacy rows or trimmed audit dumps) by defaulting to an empty dict — the
    only consumer that needs it is the UUID-provenance check, which will
    simply find no UUIDs in that turn."""
    return TurnRecord(
        turn=int(d["turn"]),
        thought=str(d.get("thought", "")),
        action=dict(d.get("action") or {}),
        observation_summary=str(d.get("observation_summary", "")),
        observation_raw=dict(d.get("observation_raw") or {}),
    )


# ---------------------------------------------------------------------------
# Post-commit synthesised turn
# ---------------------------------------------------------------------------

def synth_commit_record(
    prior_history: tuple[TurnRecord, ...] | list[TurnRecord],
    *,
    prepared_action_id: str,
    capability: str,
    cr: ConfirmResult,
) -> TurnRecord:
    """Build the COMMITTED TurnRecord the next run_agent call MUST see for the
    CONTINUATION rule to fire. Summary wording is unambiguous on purpose: §3
    of the system prompt keys on the literal prefix 'COMMITTED action_id='."""
    last_turn = prior_history[-1].turn if prior_history else 0
    summary = (
        f"COMMITTED action_id={prepared_action_id} capability={capability!r} — "
        f"the write LANDED in the database. Do NOT prepare this again."
    )
    return TurnRecord(
        turn=last_turn + 1,
        thought="(harness) commit landed",
        action={"type": "committed",
                "action_id": prepared_action_id,
                "capability": capability},
        observation_summary=summary,
        observation_raw=_safe_jsonify({
            "kind": "committed",
            "action_id": prepared_action_id,
            "capability": capability,
            "result": cr.result,
        }),
    )


# ---------------------------------------------------------------------------
# Post-cancel synthesised turn
# ---------------------------------------------------------------------------

def synth_cancel_record(
    prior_history: tuple[TurnRecord, ...] | list[TurnRecord],
    *,
    prepared_action_id: str,
    capability: str,
) -> TurnRecord:
    """Build a CANCELLED TurnRecord so the LLM's next call SEES the user
    rejected this specific prepare. Without it, the LLM only sees the bare
    prepare in the transcript and may blindly re-emit the identical prepare on
    its next turn. The summary text tells the LLM what to do: don't repeat
    this exact prepare; if you must try again, change something (different
    inputs, ask the user first, or pick a different capability)."""
    last_turn = prior_history[-1].turn if prior_history else 0
    summary = (
        f"CANCELLED action_id={prepared_action_id} capability={capability!r} — "
        f"the human declined this prepare. Do NOT re-emit the same prepare "
        f"with the same inputs. If you still believe a write is needed, ask "
        f"the user what to change, or pick a different capability."
    )
    return TurnRecord(
        turn=last_turn + 1,
        thought="(harness) prepare cancelled by user",
        action={"type": "cancelled",
                "action_id": prepared_action_id,
                "capability": capability},
        observation_summary=summary,
        observation_raw={
            "kind": "cancelled",
            "action_id": prepared_action_id,
            "capability": capability,
        },
    )
