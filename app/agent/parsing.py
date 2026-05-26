"""LLM read-query JSON -> ReadQuery (with per-field type coercion).

Single source of truth for the read-query JSON shape — ask.py used to host
these helpers; they live here now so the agent loop is the only consumer.

The parser is deliberately FORGIVING in one direction (`operator` is accepted
as an alias for `op` — the natural English word LLMs reach for) and
DELIBERATELY STRICT in the other (unknown keys produce teaching errors that
name the bad key and list the allowed ones, instead of silently passing through
None and crashing later with a misleading message). Both pieces exist so the
agent loop's retry actually has something to fix.
"""

from __future__ import annotations

import difflib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from app.read_model.compiler import Aggregation, Filter, ReadQuery, SortKey
from app.read_model.schema import SCHEMA, FieldType


# ---------------------------------------------------------------------------
# Parse-time error (specific code so the agent loop can surface it cleanly).
# ---------------------------------------------------------------------------

class ReadQueryParseError(Exception):
    """Raised by parse_read_query when the LLM's JSON has a wrong-key /
    missing-key shape problem. Surfaced as a teaching error to the agent."""

    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"[{code}] {message}")

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


# ---------------------------------------------------------------------------
# Allowed keys + aliases per sub-shape. Aliases exist for keys LLMs commonly
# substitute; everything else is rejected with a teaching error.
# ---------------------------------------------------------------------------

_FILTER_KEYS = ("field", "op", "value")
_FILTER_ALIASES = {"operator": "op"}     # LLMs reach for the English word

_SORT_KEYS = ("field", "direction")
_SORT_ALIASES: dict[str, str] = {}

_AGG_KEYS = ("func", "field")
_AGG_ALIASES = {"function": "func"}      # symmetric with operator->op

# Top-level keys MUST be in this whitelist. The previous policy of silently
# ignoring unknown top-level keys was a source of catastrophic silent
# failures: the LLM would emit `where: {field: {op: value}}` (the natural
# guess from training data) instead of the canonical `filters: [...]`, the
# unknown `where` was silently dropped, and the resulting ReadQuery had no
# WHERE clause — returning the ENTIRE TABLE instead of the filtered subset.
# A teaching error here costs one extra turn; the silent failure leaked
# whole datasets into the LLM's context.
#
# Top-level aliases let the LLM use the natural English variants ("where"
# for "filters", "order_by" for "sort") and have them silently renamed.
# Any key that's neither canonical nor a recognised alias raises.
_TOP_LEVEL_KEYS = (
    "entity", "filters", "sort", "limit", "select",
    "aggregations", "group_by",
)
_TOP_LEVEL_ALIASES = {
    "where": "filters",       # what every LLM guesses first
    "order_by": "sort",
    "orderby": "sort",
    "group": "group_by",      # short form
}


def _apply_aliases(d: dict, aliases: dict[str, str]) -> dict:
    """Return a copy of d with alias keys renamed to their canonical names.
    If both alias and canonical are present, the canonical wins and the alias
    is silently dropped (rather than producing two values for one slot)."""
    out = dict(d)
    for alias, canonical in aliases.items():
        if alias in out:
            if canonical not in out:
                out[canonical] = out.pop(alias)
            else:
                del out[alias]
    return out


def _suggest(bad: str, allowed: tuple[str, ...]) -> str:
    """Return a 'did you mean ...?' suffix if a close match exists."""
    hit = difflib.get_close_matches(bad, list(allowed), n=1, cutoff=0.6)
    return f" Did you mean {hit[0]!r}?" if hit else ""


def _check_keys(d: dict, allowed: tuple[str, ...], what: str, location: str) -> None:
    """Raise ReadQueryParseError naming any key in d that isn't in allowed."""
    for k in d.keys():
        if k not in allowed:
            raise ReadQueryParseError(
                "unknown_key",
                f"unknown {what} key {k!r} at {location}.{_suggest(k, allowed)} "
                f"Allowed keys: {list(allowed)}.",
                {"key": k, "where": location, "allowed": list(allowed)},
            )


def _coerce_scalar(ftype: FieldType, v: Any) -> Any:
    if v is None:
        return None
    try:
        if ftype is FieldType.INTEGER:
            return int(v)
        if ftype is FieldType.DECIMAL:
            return Decimal(str(v))
        if ftype is FieldType.DATE:
            return date.fromisoformat(v) if isinstance(v, str) else v
        if ftype is FieldType.DATETIME:
            return datetime.fromisoformat(v) if isinstance(v, str) else v
        if ftype is FieldType.BOOLEAN:
            if isinstance(v, bool):
                return v
            return str(v).strip().lower() in ("true", "1", "yes")
        return str(v)
    except Exception:
        return v


def _coerce(fd, v: Any) -> Any:
    if isinstance(v, list):
        return [_coerce_scalar(fd.type, x) for x in v]
    return _coerce_scalar(fd.type, v)


def _field_index(entity: str) -> dict:
    edef = SCHEMA.get(entity)
    if edef is None:
        return {}
    idx = {fd.name: fd for fd in edef.fields}
    idx.update({fd.name: fd for fd in edef.virtual_fields})
    for j in edef.joins:
        for fd in j.exposes:
            idx[fd.name] = fd
    return idx


def parse_read_query(d: dict) -> ReadQuery:
    """Parse a read-query JSON dict into a ReadQuery, normalizing aliases and
    rejecting unknown keys with teaching errors.

    Top-level strictness — the parser REJECTS any key that isn't in
    _TOP_LEVEL_KEYS (after alias normalisation). Previously these were
    silently ignored, which let the LLM's natural `where: {…}` guess sail
    through with zero filters applied. See the comment on _TOP_LEVEL_KEYS
    for the bug-A history."""
    if not isinstance(d, dict):
        raise ReadQueryParseError(
            "bad_query", f"read query must be a JSON object, got {type(d).__name__}.",
        )

    # --- top-level: alias-normalise, then strict-validate ---
    # IMPORTANT: alias normalisation includes "where" -> "filters". If a query
    # arrives with BOTH "where" and "filters", the canonical "filters" wins
    # and "where" is dropped (per _apply_aliases). The structural validation
    # of the renamed key still has to pass below — if the LLM emitted
    # `where: {field: {op: value}}` (a dict, not a list), the rename produces
    # `filters: {…}` which then fails the "must be a list" check with a
    # teaching error that names the right shape.
    d = _apply_aliases(d, _TOP_LEVEL_ALIASES)
    _check_keys(d, _TOP_LEVEL_KEYS, what="query", location="top-level")

    entity = d.get("entity")
    idx = _field_index(entity) if entity else {}

    # --- filters ---
    raw_filters = d.get("filters") or []
    if not isinstance(raw_filters, list):
        # Most common arrival path here: LLM emitted `where: {field: {op: value}}`
        # which the alias rename turned into `filters: {…}` (a dict). The error
        # must show the CORRECT shape verbatim so the loop's next turn can copy it.
        raise ReadQueryParseError(
            "bad_filters",
            "'filters' must be a LIST of {field, op, value} objects, not a dict. "
            "Example: \"filters\": [{\"field\": \"status\", \"op\": \"=\", "
            "\"value\": \"pending\"}, {\"field\": \"scheduled_at\", "
            "\"op\": \"between\", \"value\": [\"2026-05-25\", \"2026-05-25\"]}].",
            {"got_type": type(raw_filters).__name__},
        )
    filters: list[Filter] = []
    for i, f in enumerate(raw_filters):
        if not isinstance(f, dict):
            raise ReadQueryParseError(
                "bad_filter",
                f"filters[{i}] must be a JSON object with keys {list(_FILTER_KEYS)}, "
                f"got {type(f).__name__}.",
                {"index": i},
            )
        norm = _apply_aliases(f, _FILTER_ALIASES)
        _check_keys(norm, _FILTER_KEYS, what="filter", location=f"filters[{i}]")
        if "field" not in norm:
            raise ReadQueryParseError(
                "missing_filter_field",
                f"filters[{i}] is missing required key 'field'. "
                f"A filter needs at least 'field' and 'op'.",
                {"index": i, "got_keys": list(f.keys())},
            )
        if "op" not in norm:
            raise ReadQueryParseError(
                "missing_filter_op",
                f"filters[{i}] is missing required key 'op' (the comparison operator: "
                f"'=', '<', '<=', '>', '>=', 'between', 'in', 'contains'). "
                f"'operator' is accepted as an alias for 'op'.",
                {"index": i, "got_keys": list(f.keys())},
            )
        fd = idx.get(norm["field"])
        value = norm.get("value")
        if fd is not None:
            value = _coerce(fd, value)
        filters.append(Filter(field=norm["field"], op=norm["op"], value=value))

    # --- sort ---
    raw_sort = d.get("sort") or []
    if not isinstance(raw_sort, list):
        raise ReadQueryParseError(
            "bad_sort", "'sort' must be a list of {field, direction} objects.",
        )
    sort: list[SortKey] = []
    for i, s in enumerate(raw_sort):
        if not isinstance(s, dict):
            raise ReadQueryParseError(
                "bad_sort_entry",
                f"sort[{i}] must be a JSON object with keys {list(_SORT_KEYS)}, "
                f"got {type(s).__name__}.",
                {"index": i},
            )
        norm = _apply_aliases(s, _SORT_ALIASES)
        _check_keys(norm, _SORT_KEYS, what="sort", location=f"sort[{i}]")
        if "field" not in norm:
            raise ReadQueryParseError(
                "missing_sort_field",
                f"sort[{i}] is missing required key 'field'.",
                {"index": i, "got_keys": list(s.keys())},
            )
        sort.append(SortKey(field=norm["field"], direction=norm.get("direction", "asc")))

    # --- aggregations ---
    raw_aggs = d.get("aggregations") or []
    if not isinstance(raw_aggs, list):
        raise ReadQueryParseError(
            "bad_aggregations", "'aggregations' must be a list of {func, field?} objects.",
        )
    aggregations: list[Aggregation] = []
    for i, a in enumerate(raw_aggs):
        if not isinstance(a, dict):
            raise ReadQueryParseError(
                "bad_aggregation_entry",
                f"aggregations[{i}] must be a JSON object with keys {list(_AGG_KEYS)}, "
                f"got {type(a).__name__}.",
                {"index": i},
            )
        norm = _apply_aliases(a, _AGG_ALIASES)
        _check_keys(norm, _AGG_KEYS, what="aggregation", location=f"aggregations[{i}]")
        if "func" not in norm:
            raise ReadQueryParseError(
                "missing_aggregation_func",
                f"aggregations[{i}] is missing required key 'func' "
                f"(one of: count, sum, avg, min, max). "
                f"'function' is accepted as an alias for 'func'.",
                {"index": i, "got_keys": list(a.keys())},
            )
        aggregations.append(Aggregation(func=norm["func"], field=norm.get("field")))

    return ReadQuery(
        entity=entity,
        filters=filters,
        sort=sort,
        limit=d.get("limit"),
        select=list(d.get("select") or []),
        aggregations=aggregations,
        group_by=list(d.get("group_by") or []),
    )


def extract_json_object(text: str) -> dict:
    """Strip any surrounding prose/markdown and return the first {...} block."""
    s = (text or "").strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"LLM returned no JSON object. Raw output:\n{s[:500]}")
    try:
        return json.loads(s[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned unparseable JSON ({exc}). Raw output:\n{s[:500]}")
