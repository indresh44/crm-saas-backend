"""Read-model query executor + result formatter.

Runs a ``CompiledQuery`` (built by ``compiler.compile_query``) against a DB
session and formats the rows into clean, JSON-safe output.

Boundary:
  - The compiler is pure (builds the statement, no DB). This module is the only
    place that touches a ``Session``.
  - Column restriction is already enforced upstream: the compiler projects ONLY
    schema-exposed columns, so excluded fields (e.g. ``leads.notes``) never leave
    the database. This module keys output strictly by ``compiled.output_fields``
    as defense-in-depth — it cannot surface a column the projection didn't fetch.

Out of scope (rejected/ignored upstream, not handled here): virtual/computed
fields, display/label mapping (e.g. status -> "Quote"), and aggregation. There is
no total row count — counting is aggregation; pagination reports only the rows
returned for this page.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.read_model.compiler import CompiledQuery


@dataclass(frozen=True)
class QueryResult:
    """Result of a normal entity-row query. ``kind == "rows"``."""

    entity: str
    fields: tuple[str, ...]          # projected columns, in order
    rows: list[dict[str, Any]]       # one dict per row, keyed by field name
    limit: int
    offset: int

    @property
    def returned(self) -> int:
        return len(self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "rows",
            "entity": self.entity,
            "fields": list(self.fields),
            "rows": self.rows,
            "pagination": {
                "limit": self.limit,
                "offset": self.offset,
                "returned": self.returned,
                # No "total": a count is aggregation.
            },
        }


@dataclass(frozen=True)
class AggregateResult:
    """Result of an aggregate query — group keys + aggregate values, NOT entity
    rows. ``kind == "aggregate"`` so the consumer can tell the two apart. Each row
    dict is keyed by group-key field names then aggregate aliases (e.g. "count",
    "sum_balance")."""

    entity: str
    group_by: tuple[str, ...]        # group-key field names (may be empty -> single total row)
    aggregates: tuple[str, ...]      # aggregate output aliases
    rows: list[dict[str, Any]]
    limit: int
    offset: int

    @property
    def returned(self) -> int:
        return len(self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "aggregate",
            "entity": self.entity,
            "group_by": list(self.group_by),
            "aggregates": list(self.aggregates),
            "rows": self.rows,
            "pagination": {
                "limit": self.limit,
                "offset": self.offset,
                "returned": self.returned,
            },
        }


def execute_query(compiled: CompiledQuery, session: Session):
    """Execute the compiled statement and format the result.

    Returns an ``AggregateResult`` for aggregate queries and a ``QueryResult`` for
    normal row queries — distinguishable by type and by the ``kind`` field of
    ``to_dict()``. Both key rows strictly by ``compiled.output_fields``.
    """
    result = session.execute(compiled.statement)
    mappings = result.mappings().all()

    rows: list[dict[str, Any]] = [
        {name: _to_jsonable(mapping[name]) for name in compiled.output_fields}
        for mapping in mappings
    ]

    if compiled.is_aggregate:
        return AggregateResult(
            entity=compiled.entity,
            group_by=compiled.group_fields,
            aggregates=compiled.aggregate_aliases,
            rows=rows,
            limit=compiled.applied_limit,
            offset=compiled.applied_offset,
        )

    return QueryResult(
        entity=compiled.entity,
        fields=compiled.output_fields,
        rows=rows,
        limit=compiled.applied_limit,
        offset=compiled.applied_offset,
    )


def _to_jsonable(value: Any) -> Any:
    """Coerce a DB value to a JSON-safe scalar. Serialization only — never a
    display/label transform (e.g. enum -> raw .value, not a human label)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):     # datetime is a subclass of date
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)                        # string preserves money precision (not float)
    if isinstance(value, UUID):
        return str(value)
    return value
