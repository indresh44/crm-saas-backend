"""Turn record + deterministic observation summarization + UUID-provenance check.

Summaries are TEMPLATED — no extra LLM call. observation_raw stays local (for
audit/debug); only observation_summary is fed back to the LLM next turn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# UUID detection used by the prepare-time provenance check.
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

#: How many rows we keep in observation_raw — must stay >= SUMMARY_CAP so the
#: UUID-provenance check can verify every UUID the LLM saw in its summary.
READ_ROWS_FULL_CAP = 20
#: How many rows we include in the LLM-visible summary. Raised from 3 to 20
#: so tabular questions ("show me all pending follow-ups") can be answered in
#: ONE turn instead of forcing the LLM into scheduled_at-cursor pagination
#: it cannot reliably converge. The compiler's HARD_ROW_CAP (50) still bounds
#: the underlying query.
READ_ROWS_SUMMARY_CAP = 20
#: Per-field string truncation in the summary.
_FIELD_VALUE_CAP = 80


@dataclass(frozen=True)
class TurnRecord:
    turn: int
    thought: str
    action: dict                 # the LLM's chosen action JSON (verbatim)
    observation_summary: str     # what the NEXT turn's LLM sees
    observation_raw: dict        # full data, kept locally for debug/inspection


# ---------------------------------------------------------------------------
# Summary templates
# ---------------------------------------------------------------------------

def summarize_read_success(entity: str, rows: list[dict]) -> tuple[str, dict]:
    """Build (summary, raw) for a successful read. The summary EXPLICITLY says
    'showing N — narrow filter or ask user' when the total exceeds the summary cap."""
    total = len(rows)
    full_rows = rows[:READ_ROWS_FULL_CAP]
    raw = {"kind": "rows", "entity": entity, "count": total, "rows": full_rows}

    if total == 0:
        return f"read on {entity}: 0 rows matched.", raw

    summary_rows = rows[:READ_ROWS_SUMMARY_CAP]
    header = f"read on {entity}: {total} row(s) matched"
    if total > READ_ROWS_SUMMARY_CAP:
        header += f", showing {READ_ROWS_SUMMARY_CAP} — narrow the filter or ask the user"
    lines = [header + "."]
    for i, row in enumerate(summary_rows, 1):
        kv = ", ".join(f"{k}={_trim(v)}" for k, v in row.items())
        lines.append(f"  row {i}: {kv}")
    return "\n".join(lines), raw


def summarize_read_error(error: dict) -> tuple[str, dict]:
    """error may be {code, message, details} OR ReadModelValidationError.to_dict()
    shape ({errors: [{code, message, ...}, ...]})."""
    if "errors" in error and error["errors"]:
        first = error["errors"][0]
        code, msg = first.get("code"), first.get("message")
    else:
        code, msg = error.get("code"), error.get("message")
    return f"read failed: {code} — {msg}", {"kind": "error", **error}


def summarize_prepare_success(capability: str, action_id: str, preview: str,
                              editable_fields: list[str]) -> tuple[str, dict]:
    summary = f"prepared {capability} (action_id={action_id}): {preview}"
    raw = {"kind": "prepared", "action_id": action_id, "capability": capability,
           "preview": preview, "editable_fields": editable_fields}
    return summary, raw


def summarize_prepare_error(error: dict) -> tuple[str, dict]:
    return (
        f"prepare failed: {error.get('code')} — {error.get('message')}",
        {"kind": "error", **error},
    )


# ---------------------------------------------------------------------------
# UUID provenance — pre-prepare check
# ---------------------------------------------------------------------------

def collect_known_uuids(history: list[TurnRecord]) -> set[str]:
    """Every UUID-shaped string found anywhere in prior READ observations' rows.
    Per spec: only prior READ observations count as provenance — UUIDs in the
    user's goal text do NOT (the LLM must read-verify them first)."""
    out: set[str] = set()
    for tr in history:
        if tr.observation_raw.get("kind") != "rows":
            continue
        for row in tr.observation_raw.get("rows", []):
            _scan_uuids_into(row, out)
    return out


def find_unknown_uuid_inputs(inputs: dict, known: set[str]) -> list[tuple[str, str]]:
    """Return [(field_name, uuid_string)] for any UUID-shaped value in `inputs`
    not present in `known`. Empty list = all UUID inputs have provenance."""
    bad: list[tuple[str, str]] = []
    for k, v in inputs.items():
        if isinstance(v, str) and UUID_RE.match(v) and v not in known:
            bad.append((k, v))
    return bad


def _scan_uuids_into(value: Any, out: set[str]) -> None:
    if isinstance(value, str):
        if UUID_RE.match(value):
            out.add(value)
        return
    if isinstance(value, dict):
        for v in value.values():
            _scan_uuids_into(v, out)
        return
    if isinstance(value, (list, tuple)):
        for v in value:
            _scan_uuids_into(v, out)


def _trim(v: Any) -> str:
    s = str(v)
    return s if len(s) <= _FIELD_VALUE_CAP else s[:_FIELD_VALUE_CAP] + "…"
