"""System-prompt construction + per-turn message rendering.

Schema and capability context are built programmatically from schema.py and
the registry — single source of truth. ask.py imports these (no duplicated
prompt copies)."""

from __future__ import annotations

import json
from typing import Any

from app.read_model.schema import SCHEMA
from app.write_surface.registry import CAPABILITY_REGISTRY


# ---------------------------------------------------------------------------
# READ schema context (entities, fields, ops, enum values, joins, virtuals)
# ---------------------------------------------------------------------------

def _ops(fd) -> str:
    return f"  [ops: {', '.join(o.value for o in fd.allowed_operators)}]"


def _vals(fd) -> str:
    return f"  [values: {', '.join(fd.enum_values)}]" if fd.enum_values else ""


def build_schema_context() -> str:
    lines: list[str] = []
    for ename, edef in SCHEMA.items():
        lines.append(f"ENTITY {ename!r}:")
        lines.append("  RAW fields — returned by default; usable in filters/sort/group_by/select:")
        for fd in edef.fields:
            lines.append(f"    - {fd.name}: {fd.type.value}{_ops(fd)}{_vals(fd)}")
        if edef.virtual_fields:
            lines.append(
                "  VIRTUAL (computed) fields — returned ONLY if named in select; "
                "also usable in filters/sort/group_by/aggregate:"
            )
            for fd in edef.virtual_fields:
                lines.append(f"    - {fd.name}: {fd.type.value}{_ops(fd)}{_vals(fd)}")
        joined: list[str] = []
        for j in edef.joins:
            for fd in j.exposes:
                joined.append(f"    - {fd.name}: {fd.type.value}{_ops(fd)} (via {j.name} join)")
        if joined:
            lines.append("  JOINED fields — returned ONLY if named in select (or filtered/sorted on):")
            lines.extend(joined)
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# WRITE capability context
# ---------------------------------------------------------------------------

def build_capability_context() -> str:
    lines = ["WRITE CAPABILITIES (use action.type='prepare' + the chosen name):"]
    for name, decl in CAPABILITY_REGISTRY.items():
        lines.append(f"  - {name}: {decl.description}")
        lines.append("    inputs:")
        for s in decl.inputs:
            tag = "LOCKED" if s.locked else "EDITABLE"
            req = "required" if s.required else "optional"
            enum_str = f"  values: {', '.join(s.enum_values)}" if s.enum_values else ""
            lines.append(f"      - {s.name} ({s.type}, {req}, {tag}){enum_str}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_TEMPLATE = """You are an agent that drives a business CRM. Each turn you emit ONE structured action.

The user gives you a GOAL. You loop with a hard cap of 8 turns. Each turn:
  - Reason briefly in "thought".
  - Choose ONE action: read | prepare | ask_user | done.

ACTION ENVELOPE — emit ONLY this JSON object (no prose, no markdown fences):

{
  "thought": "<one or two sentences>",
  "action": { "type": "read",     "query": { <ReadQuery JSON, see SCHEMA below> } }
}
{
  "thought": "...",
  "action": { "type": "prepare",  "capability": "<name>", "inputs": { ... } }
}
{
  "thought": "...",
  "action": { "type": "ask_user", "question": "<text to ask the human>" }
}
{
  "thought": "...",
  "action": { "type": "done",     "answer": "<final answer to the user>" }
}

RULES — follow exactly:
- Output ONLY the JSON envelope — no prose, no markdown fences, no comments.
- "thought" is required and must be a string.
- READ: use ONLY entities, fields, operators, and enum values listed in the SCHEMA below.
  Each read returns a deterministic summary back to you: count + up to 3 rows. If more than
  3 rows match, the summary tells you so — narrow filters or ask the user.
- PREPARE: use ONLY a capability name listed under CAPABILITIES, with its declared inputs.
  EVERY UUID-shaped input value (invoice_id, lead_id, stage_id, customer_id, new_stage_id,
  etc.) MUST appear in a PRIOR READ OBSERVATION in this run. If you don't have the UUID
  from a prior read, do a READ first to look it up. NEVER invent a UUID — even one from the
  user's goal text must be read-verified first. After a PREPARE the loop will stop and the
  human will confirm separately — do not emit a DONE turn after a prepare.
- ASK_USER: only when you genuinely cannot proceed without human input (ambiguity, missing
  data the read model can't supply).
- DONE: when the user's goal has been read-answered and no write is needed.
- Do NOT emit the same action two turns in a row (the loop will abort if you do).
- CONTINUATION — turns marked "(PRIOR CALL — already completed)" in the transcript
  are work that finished in EARLIER loop runs for this same goal. They are ground
  truth and must not be redone:
    * Do NOT re-issue a read whose answer is already visible in a prior turn's
      observation summary. Quote the value from the summary instead.
    * Do NOT prepare a write that a prior turn already shows as COMMITTED. A
      synthetic turn with action.type "committed" and an observation starting
      "COMMITTED action_id=..." means the human ALREADY CONFIRMED and the write
      ALREADY LANDED in the database. Preparing the same write again would
      create a DUPLICATE (e.g. a second payment, a second invoice). The loop
      only resumes after a successful commit or after the user answered an
      ask_user — there is no other reason a COMMITTED turn would be in your
      transcript.
    * If a prior turn shows a prepare followed by a synthetic turn with
      action.type "cancelled" and an observation starting "CANCELLED
      action_id=...", the human REJECTED that exact prepare. Do NOT re-emit
      the SAME prepare (same capability + same inputs). If you still believe a
      write is needed, either ASK_USER what to change, or pick a different
      capability/inputs. Repeating the rejected prepare is a hard mistake.
    * If the prior turns together already satisfy the goal, emit DONE on turn 1
      summarising what was done. Do not run a "verification" read first — the
      COMMITTED summary is the verification.
"""


def build_system_prompt(schema_ctx: str, capability_ctx: str) -> str:
    return _SYSTEM_TEMPLATE + "\n\nSCHEMA (for READ):\n" + schema_ctx + "\n\n" + capability_ctx


# ---------------------------------------------------------------------------
# Per-turn message rendering
# ---------------------------------------------------------------------------

def render_history(goal: str, history, prior_count: int = 0) -> list[dict[str, Any]]:
    """Single user message: the goal + a compact transcript of prior turns
    (thought + action + observation SUMMARY — never raw rows).

    `prior_count` is how many of `history`'s leading entries came from EARLIER
    run_agent calls for this same task. Those turns are tagged inline so the
    LLM can apply the CONTINUATION rule (do not redo completed work)."""
    if not history:
        return [{"role": "user", "content": f"GOAL:\n{goal}"}]
    lines = [f"GOAL:\n{goal}", "", "TRANSCRIPT SO FAR:"]
    for i, tr in enumerate(history):
        tag = " (PRIOR CALL — already completed)" if i < prior_count else ""
        lines.append(f"-- turn {tr.turn}{tag} --")
        lines.append(f"thought: {tr.thought}")
        lines.append(f"action: {json.dumps(tr.action)}")
        lines.append(f"observation: {tr.observation_summary}")
    lines.append("")
    lines.append("Now emit the next action.")
    return [{"role": "user", "content": "\n".join(lines)}]
