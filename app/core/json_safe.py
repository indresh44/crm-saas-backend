"""JSON-safety helper.

`safe_jsonify(value)` recursively converts a value to something `json.dumps`
accepts WITHOUT a `default=` hook. Used at the boundary where Python objects
(Decimal, UUID, datetime, …) cross into JSONB columns or API payloads.

This is the same helper the agent loop has used since the chat-service Stage-1
work; it's lifted to `app/core/` so business services (invoice, lead, payment)
can call it without importing from `app/agent/` — that would be the wrong
layering direction. `app/agent/serialize.py:_safe_jsonify` is now a re-export
of this module's `safe_jsonify` for backwards compatibility with existing
imports.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


def safe_jsonify(value: Any) -> Any:
    """Return a value `json.dumps`-able without a default= hook.

    Conversion rules:
      * None / bool / int / float / str — pass through.
      * Decimal — str(value) (preserves precision for money columns).
      * datetime / date — ISO 8601 string.
      * UUID — str(value).
      * dict — recursive, keys coerced to str.
      * list / tuple — recursive (tuples become lists; JSON has no tuple).
      * anything else — last-ditch repr(). Better than raising mid-commit.
        If this fires for a real production value, tighten the call site
        rather than relaxing the rule further."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): safe_jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_jsonify(v) for v in value]
    return str(value)
