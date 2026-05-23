"""Read-model query compiler — happy-path core.

Takes a structured, untrusted read request, validates it **completely** against
the ``schema.py`` whitelist, and builds a safe parameterized SQLModel ``select``
statement. This is the security-critical boundary of the read engine.

Scope of THIS module (deliberately narrow):
  - Filters (operators gated by field type via ``OPERATORS_BY_TYPE``)
  - Sorting
  - Limit / offset pagination
  - Single-hop joins only (inferred from referenced join-exposed fields)
  - Virtual / computed fields, resolved to SQL via ``virtual_fields.py`` — usable
    in filters, sorts, and (when referenced) projected into output
  - The four non-negotiable enforcements (see ``compile_query``)

Explicitly NOT here (later steps), and rejected with specific errors:
  - The two-hop invoices->customer join -> JOIN_NOT_SUPPORTED
  - Aggregations (count / sum / group by)
  - Display / label mapping (e.g. status -> "Quote")
  - A raw-field select list (only virtual fields have an explicit request list)

Build vs. execute boundary:
  ``compile_query`` is pure — it validates and builds, and returns a
  ``CompiledQuery`` holding the statement. It NEVER touches a ``Session`` or the
  database. Execution and result-formatting are separate steps that consume
  ``CompiledQuery.statement`` without modifying this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import func
from sqlmodel import select

from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.lead import Lead
from app.models.pipeline import PipelineStage
from app.read_model.schema import (
    DEFAULT_LIMIT,
    HARD_ROW_CAP,
    EntityDef,
    FieldDef,
    JoinDef,
    Operator,
    FieldType,
    SCHEMA,
)
from app.read_model.virtual_fields import (
    VIRTUAL_RESOLVERS,
    ResolverContext,
    VirtualResolver,
)


# ===========================================================================
# Binding layer — abstract schema names -> concrete ORM classes/columns.
# schema.py stores model classes as strings and joins by table name; the
# compiler needs the real SQLModel classes and the underlying column for each
# join-exposed field. This is the ONLY coupling to app/models; schema.py is
# never modified.
# ===========================================================================

#: Query-facing entity name -> root SQLModel class.
_ENTITY_MODELS: dict[str, type] = {
    "leads": Lead,
    "invoices": Invoice,
    "customers": Customer,   # Bound for layer-6 customer-resolution demo (added Step "Layer 6").
}

#: (entity, join name) -> (final target SQLModel class, {exposed_field_name: target column attr}).
#: Used to resolve a joined field to its column. For multi-hop joins this names the
#: FINAL table (e.g. invoices.customer -> Customer); the hop chain is in _THROUGH_JOINS.
_JOIN_BINDINGS: dict[tuple[str, str], tuple[type, dict[str, str]]] = {
    ("leads", "customer"): (Customer, {"customer_name": "name", "customer_phone": "phone"}),
    ("leads", "pipeline_stage"): (PipelineStage, {}),
    ("invoices", "lead"): (Lead, {"lead_title": "title"}),
    # Two-hop: invoice -> lead -> customer. Column resolution targets Customer; the
    # intermediate leads hop is added by _apply_joins via _THROUGH_JOINS.
    ("invoices", "customer"): (Customer, {"customer_name": "name", "customer_phone": "phone"}),
}

#: Multi-hop joins: (entity, join name) -> ordered [(target_model, ON-clause), ...].
#: Only declared, implemented multi-hop paths appear here; _resolve rejects any
#: through-join that is NOT listed (so an undeclared multi-hop can never compile).
#: The chain is anchored at the (business-scoped) root entity, so tenant isolation
#: holds by correlation — no cross-tenant row can be reached following these FKs.
_THROUGH_JOINS: dict[tuple[str, str], list[tuple[type, Any]]] = {
    ("invoices", "customer"): [
        (Lead, Invoice.lead_id == Lead.id),
        (Customer, Lead.customer_id == Customer.id),
    ],
}

#: The tenant column injected on every query. Never queryable from a request.
_TENANT_COLUMN = "business_id"


# ===========================================================================
# Provisional input structure (frozen dataclasses; the formal Pydantic request
# model is a separate later step).
# ===========================================================================

@dataclass(frozen=True)
class Filter:
    field: str          # root field name OR a join-exposed field name
    op: str             # must match an Operator value, e.g. "=", "between", "in", "contains"
    value: Any          # scalar; list[2] for "between"; non-empty list for "in"; str for "contains"


@dataclass(frozen=True)
class SortKey:
    field: str
    direction: str = "asc"   # "asc" | "desc"


@dataclass(frozen=True)
class Aggregation:
    func: str                       # "count" | "sum" | "avg" | "min" | "max"
    field: Optional[str] = None     # None / "*" => count rows; required for sum/avg/min/max
    # Output key is derived: "count" for count(*), else f"{func}_{field}".


@dataclass(frozen=True)
class ReadQuery:
    entity: str
    filters: list[Filter] = field(default_factory=list)
    sort: list[SortKey] = field(default_factory=list)
    limit: Optional[int] = None
    offset: Optional[int] = None
    # Explicit output projection. Names may be raw, virtual, or joined-exposed fields
    # declared in schema.py for the entity (order preserved, duplicates de-duped).
    #   - present  -> output is EXACTLY these columns, in order.
    #   - empty     -> default: the entity's raw fields from schema.py, plus exposed
    #                 fields of any join referenced by a filter/sort (current behavior).
    # Naming a joined/virtual field pulls in its single-hop join (same as filters).
    # NOT allowed together with aggregations.
    select: list[str] = field(default_factory=list)
    # Aggregation mode: non-empty `aggregations` (optionally with `group_by`)
    # switches the query from returning entity rows to returning aggregate rows
    # (group keys + aggregate values). `group_by` without aggregations is rejected.
    aggregations: list[Aggregation] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)


# ===========================================================================
# Structured, machine-legible errors. All validation problems are aggregated
# and raised together so the assistant can correct everything in one pass.
# ===========================================================================

class ErrorCode(str, Enum):
    UNKNOWN_ENTITY = "unknown_entity"
    BUSINESS_ID_NOT_QUERYABLE = "business_id_not_queryable"
    UNKNOWN_FIELD = "unknown_field"
    VIRTUAL_FIELD_NOT_SUPPORTED = "virtual_field_not_supported"
    NOT_A_VIRTUAL_FIELD = "not_a_virtual_field"
    JOIN_NOT_SUPPORTED = "join_not_supported"
    DISALLOWED_OPERATOR = "disallowed_operator"
    BAD_FILTER_VALUE = "bad_filter_value"
    INVALID_ENUM_VALUE = "invalid_enum_value"
    BAD_SORT = "bad_sort"
    BAD_PAGINATION = "bad_pagination"
    UNKNOWN_AGGREGATE_FUNC = "unknown_aggregate_func"
    AGGREGATE_FIELD_REQUIRED = "aggregate_field_required"
    AGGREGATE_TYPE_MISMATCH = "aggregate_type_mismatch"
    GROUP_BY_REQUIRES_AGGREGATION = "group_by_requires_aggregation"
    AGGREGATE_MODE_CONFLICT = "aggregate_mode_conflict"
    DUPLICATE_OUTPUT_FIELD = "duplicate_output_field"


@dataclass(frozen=True)
class FieldError:
    """One specific validation problem, located precisely within the request."""

    code: ErrorCode
    message: str
    location: str               # e.g. "entity", "filters[2].field", "sort[0]", "limit"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "location": self.location, "details": self.details}


class ReadModelValidationError(Exception):
    """Raised when a request fails validation. Carries every problem found."""

    def __init__(self, errors: list[FieldError]):
        self.errors = errors
        joined = "; ".join(f"[{e.code.value}] {e.location}: {e.message}" for e in errors)
        super().__init__(f"read-model request rejected ({len(errors)} error(s)): {joined}")

    def to_dict(self) -> dict[str, Any]:
        return {"errors": [e.to_dict() for e in self.errors]}


# ===========================================================================
# Compiled output — the build/execute hand-off artifact.
# ===========================================================================

@dataclass(frozen=True)
class CompiledQuery:
    entity: str
    statement: Any              # SQLModel/SQLAlchemy select statement; execution is a separate step
    output_fields: tuple[str, ...]   # projected columns (ordered) — the ONLY fields that leave the DB
    applied_limit: int
    applied_offset: int
    joins_used: tuple[str, ...]
    business_id: UUID
    # Aggregate mode metadata. is_aggregate=False => normal entity-row query.
    # When True, output_fields == group_fields + aggregate_aliases.
    is_aggregate: bool = False
    group_fields: tuple[str, ...] = ()
    aggregate_aliases: tuple[str, ...] = ()


# ===========================================================================
# Internal validated plan (only built when there are zero errors).
# ===========================================================================

@dataclass(frozen=True)
class _Resolved:
    """A reference resolved to its source.

    Exactly one of these shapes holds:
      - root field:    ``join is None`` and ``virtual is None``
      - joined field:  ``join`` set
      - virtual field: ``virtual`` set (joins it needs come from the resolver)
    """

    field_def: FieldDef
    join: Optional[JoinDef]
    virtual: Optional[VirtualResolver] = None


@dataclass(frozen=True)
class _PlanFilter:
    resolved: _Resolved
    op: Operator
    value: Any


@dataclass(frozen=True)
class _PlanSort:
    resolved: _Resolved
    descending: bool


# ===========================================================================
# Public entry point
# ===========================================================================

def compile_query(
    request: ReadQuery,
    *,
    business_id: UUID,
    now: Optional[datetime] = None,
) -> CompiledQuery:
    """Validate ``request`` against the schema whitelist and build a safe query.

    The four non-negotiable enforcements:
      1. business_id injection — taken ONLY from the ``business_id`` argument
         (never from the request); a ``business_id`` predicate is added to the
         root entity AND to every sub-query/join the virtual resolvers introduce
         (explicitly where the table has the column, by correlation otherwise).
         The request cannot express it (and is rejected if it tries).
      2. Whitelist validation — every entity, field, operator, sort key, join, and
         virtual field is validated against schema.py; anything undeclared is rejected.
      3. Hard row cap — every query gets a LIMIT; a requested limit above
         HARD_ROW_CAP is clamped (not rejected); a missing limit gets DEFAULT_LIMIT.
      4. Parameterized only — all values bind via SQLAlchemy expressions; no value
         is ever string-interpolated into SQL.

    ``now`` is the single injected, business-local clock for time-relative virtual
    fields (``is_overdue``, ``days_overdue``, ``has_overdue_followup``); ``today``
    is derived as ``now.date()``. If a time-relative field is referenced and ``now``
    is None, a ``ValueError`` is raised (caller misconfiguration, not request error).

    Raises ``ReadModelValidationError`` (with every problem found) on failure.
    """
    # --- 1. Entity (fatal: cannot validate fields without a known entity) ---
    entity_def = SCHEMA.get(request.entity)
    if entity_def is None:
        raise ReadModelValidationError([
            FieldError(
                ErrorCode.UNKNOWN_ENTITY,
                f"unknown entity {request.entity!r}; allowed: {sorted(SCHEMA)}",
                "entity",
                {"entity": request.entity, "allowed": sorted(SCHEMA)},
            )
        ])

    index = _FieldIndex(entity_def)
    errors: list[FieldError] = []
    plan_filters: list[_PlanFilter] = []
    plan_sorts: list[_PlanSort] = []

    # --- 2. Filters ---
    for i, flt in enumerate(request.filters):
        loc = f"filters[{i}]"
        resolved = _resolve(index, flt.field, f"{loc}.field", errors)
        if resolved is None:
            continue  # field problem already recorded; dependent checks skipped

        op = _resolve_operator(flt.op, resolved.field_def, f"{loc}.op", errors)
        if op is None:
            continue

        if _validate_value(op, flt.value, resolved.field_def, f"{loc}.value", errors):
            plan_filters.append(_PlanFilter(resolved, op, flt.value))

    # --- 3. Mode: aggregate (aggregations / group_by) vs. row ---
    is_aggregate = bool(request.aggregations) or bool(request.group_by)
    plan_aggs: list[tuple[str, str, Optional[_Resolved]]] = []
    plan_groups: list[tuple[str, _Resolved]] = []
    selected: list[tuple[str, _Resolved]] = []

    if is_aggregate:
        _validate_aggregate(request, index, plan_aggs, plan_groups, errors)
    else:
        _validate_sort(request, index, plan_sorts, errors)
        _validate_select(request, index, selected, errors)

    # --- 4. Pagination (both modes) ---
    if request.limit is not None and request.limit < 1:
        errors.append(FieldError(
            ErrorCode.BAD_PAGINATION, f"limit must be >= 1, got {request.limit}", "limit", {"limit": request.limit},
        ))
    if request.offset is not None and request.offset < 0:
        errors.append(FieldError(
            ErrorCode.BAD_PAGINATION, f"offset must be >= 0, got {request.offset}", "offset", {"offset": request.offset},
        ))

    # --- Fail before building anything ---
    if errors:
        raise ReadModelValidationError(errors)

    # Clock requirement (caller misconfiguration, not a request validation error):
    # any referenced time-relative virtual field needs an injected business-local now.
    # This must cover filters, sorts, projected virtuals, AND aggregate/group fields.
    all_resolved = (
        [pf.resolved for pf in plan_filters]
        + [ps.resolved for ps in plan_sorts]
        + [r for (_name, r) in selected]
        + [r for (_alias, _fn, r) in plan_aggs if r is not None]
        + [r for (_name, r) in plan_groups]
    )
    if now is None and any(r.virtual is not None and r.virtual.needs_clock for r in all_resolved):
        raise ValueError(
            "compile_query: a time-relative virtual field (is_overdue / days_overdue / "
            "has_overdue_followup) was referenced but no business-local 'now' was injected"
        )
    today = now.date() if now is not None else None

    model = _ENTITY_MODELS[request.entity]
    ctx = ResolverContext(
        entity=request.entity, model=model, business_id=business_id, today=today, now=now,
    )

    if is_aggregate:
        return _build_aggregate_query(
            request, entity_def, model, ctx, plan_filters, plan_aggs, plan_groups, business_id,
        )
    return _build_row_query(
        request, entity_def, model, ctx, plan_filters, plan_sorts, selected, business_id,
    )


# ===========================================================================
# Validation helpers
# ===========================================================================

class _FieldIndex:
    """Per-entity lookup of root, virtual, and join-exposed field names."""

    def __init__(self, entity_def: EntityDef):
        self.entity: str = entity_def.name
        self.root: dict[str, FieldDef] = {f.name: f for f in entity_def.fields}
        self.virtual: dict[str, FieldDef] = {f.name: f for f in entity_def.virtual_fields}
        self.join_by_name: dict[str, JoinDef] = {j.name: j for j in entity_def.joins}
        self.exposed: dict[str, tuple[JoinDef, FieldDef]] = {}
        for j in entity_def.joins:
            for f in j.exposes:
                self.exposed[f.name] = (j, f)


def _resolve(index: _FieldIndex, name: str, location: str, errors: list[FieldError]) -> Optional[_Resolved]:
    """Resolve a referenced field name; record a specific error and return None on failure."""
    if name == _TENANT_COLUMN:
        errors.append(FieldError(
            ErrorCode.BUSINESS_ID_NOT_QUERYABLE,
            "business_id is never queryable; tenant scope is injected by the compiler",
            location,
            {"field": name},
        ))
        return None

    if name in index.root:
        return _Resolved(index.root[name], join=None)

    if name in index.virtual:
        resolver = VIRTUAL_RESOLVERS.get((index.entity, name))
        if resolver is None:
            errors.append(FieldError(
                ErrorCode.VIRTUAL_FIELD_NOT_SUPPORTED,
                f"virtual field {name!r} is declared but has no resolver yet",
                location,
                {"field": name},
            ))
            return None
        # A virtual field may depend on joins; only single-hop joins are allowed.
        for jn in resolver.requires_joins:
            joindef = index.join_by_name.get(jn)
            if joindef is not None and joindef.through is not None:
                errors.append(FieldError(
                    ErrorCode.JOIN_NOT_SUPPORTED,
                    f"virtual field {name!r} needs the multi-hop {jn!r} join, which is not supported yet",
                    location,
                    {"field": name, "join": jn, "through": joindef.through},
                ))
                return None
        return _Resolved(index.virtual[name], join=None, virtual=resolver)

    if name in index.exposed:
        joindef, field_def = index.exposed[name]
        # Multi-hop joins are allowed only when their hop chain is implemented
        # (declared in _THROUGH_JOINS). Any other through-join is rejected.
        if joindef.through is not None and (index.entity, joindef.name) not in _THROUGH_JOINS:
            errors.append(FieldError(
                ErrorCode.JOIN_NOT_SUPPORTED,
                f"field {name!r} requires the multi-hop {joindef.name!r} join, which is not supported yet",
                location,
                {"field": name, "join": joindef.name, "through": joindef.through},
            ))
            return None
        return _Resolved(field_def, join=joindef)

    # Message-only improvement: name the entity and list every field the LLM
    # could legally have used. Behaviour unchanged — still rejected, same code.
    import difflib as _difflib
    available = sorted(set(index.root) | set(index.virtual) | set(index.exposed))
    hit = _difflib.get_close_matches(name, available, n=1, cutoff=0.6)
    suggestion = f" Did you mean {hit[0]!r}?" if hit else ""
    errors.append(FieldError(
        ErrorCode.UNKNOWN_FIELD,
        f"unknown field {name!r} on entity {index.entity!r}.{suggestion} "
        f"Available fields: {available}.",
        location,
        {"field": name, "entity": index.entity, "available": available},
    ))
    return None


def _resolve_operator(op_str: str, field_def: FieldDef, location: str, errors: list[FieldError]) -> Optional[Operator]:
    allowed = field_def.allowed_operators
    try:
        op = Operator(op_str)
    except ValueError:
        errors.append(FieldError(
            ErrorCode.DISALLOWED_OPERATOR,
            f"unknown operator {op_str!r}; allowed for this field: {[o.value for o in allowed]}",
            location,
            {"operator": op_str, "field": field_def.name, "allowed": [o.value for o in allowed]},
        ))
        return None
    if op not in allowed:
        errors.append(FieldError(
            ErrorCode.DISALLOWED_OPERATOR,
            f"operator {op.value!r} not allowed for {field_def.type.value} field {field_def.name!r}; "
            f"allowed: {[o.value for o in allowed]}",
            location,
            {"operator": op.value, "field": field_def.name, "type": field_def.type.value,
             "allowed": [o.value for o in allowed]},
        ))
        return None
    return op


def _validate_value(op: Operator, value: Any, field_def: FieldDef, location: str, errors: list[FieldError]) -> bool:
    """Validate value SHAPE and enum membership only (no type coercion / date parsing)."""
    is_seq = isinstance(value, (list, tuple))

    if op is Operator.BETWEEN:
        if not is_seq or len(value) != 2:
            errors.append(FieldError(
                ErrorCode.BAD_FILTER_VALUE, "'between' requires a list of exactly two values", location,
                {"operator": op.value, "value": value},
            ))
            return False
    elif op is Operator.IN:
        if not is_seq or len(value) == 0:
            errors.append(FieldError(
                ErrorCode.BAD_FILTER_VALUE, "'in' requires a non-empty list of values", location,
                {"operator": op.value, "value": value},
            ))
            return False
    elif op is Operator.CONTAINS:
        if not isinstance(value, str):
            errors.append(FieldError(
                ErrorCode.BAD_FILTER_VALUE, "'contains' requires a string value", location,
                {"operator": op.value, "value": value},
            ))
            return False
    else:  # EQ, LT, LTE, GT, GTE -> scalar
        if is_seq:
            errors.append(FieldError(
                ErrorCode.BAD_FILTER_VALUE, f"operator {op.value!r} requires a single scalar value, not a list",
                location, {"operator": op.value, "value": value},
            ))
            return False

    # Enum membership (only EQ / IN can reach an enum field, per OPERATORS_BY_TYPE).
    if field_def.type is FieldType.ENUM:
        allowed = set(field_def.enum_values or ())
        candidates = list(value) if op is Operator.IN else [value]
        bad = [v for v in candidates if v not in allowed]
        if bad:
            errors.append(FieldError(
                ErrorCode.INVALID_ENUM_VALUE,
                f"invalid value(s) {bad} for enum field {field_def.name!r}; allowed: {sorted(allowed)}",
                location,
                {"field": field_def.name, "invalid": bad, "allowed": sorted(allowed)},
            ))
            return False

    return True


# --- Aggregation validation -------------------------------------------------

#: name -> SQLAlchemy aggregate function factory.
_AGG_FUNCS = {
    "count": func.count,
    "sum": func.sum,
    "avg": func.avg,
    "min": func.min,
    "max": func.max,
}
_NUMERIC_TYPES = {FieldType.INTEGER, FieldType.DECIMAL}
_ORDERABLE_TYPES = {FieldType.INTEGER, FieldType.DECIMAL, FieldType.DATE, FieldType.DATETIME, FieldType.STRING}


def _validate_sort(request: ReadQuery, index: _FieldIndex, plan_sorts: list[_PlanSort], errors: list[FieldError]) -> None:
    for i, sk in enumerate(request.sort):
        loc = f"sort[{i}]"
        resolved = _resolve(index, sk.field, f"{loc}.field", errors)
        direction = (sk.direction or "").lower()
        if direction not in ("asc", "desc"):
            errors.append(FieldError(
                ErrorCode.BAD_SORT,
                f"sort direction must be 'asc' or 'desc', got {sk.direction!r}",
                f"{loc}.direction",
                {"direction": sk.direction},
            ))
            continue
        if resolved is None:
            continue
        plan_sorts.append(_PlanSort(resolved, descending=(direction == "desc")))


def _validate_select(
    request: ReadQuery, index: _FieldIndex, selected: list[tuple[str, _Resolved]], errors: list[FieldError],
) -> None:
    """Validate the explicit output projection. Names may be raw, virtual, or
    joined-exposed fields — resolved through the same whitelist path as filters
    (business_id rejected; unknown -> unknown_field; two-hop joined -> join_not_supported;
    virtual without resolver -> virtual_field_not_supported). Order preserved, de-duped."""
    seen: set[str] = set()
    for i, name in enumerate(request.select):
        loc = f"select[{i}]"
        resolved = _resolve(index, name, loc, errors)
        if resolved is None:
            continue
        if name in seen:
            continue  # silently de-dupe
        seen.add(name)
        selected.append((name, resolved))


def _validate_aggregate(
    request: ReadQuery,
    index: _FieldIndex,
    plan_aggs: list[tuple[str, str, Optional[_Resolved]]],
    plan_groups: list[tuple[str, _Resolved]],
    errors: list[FieldError],
) -> None:
    """Validate aggregations + group_by. Output = group keys + aggregate values, so
    raw fields can only appear via group_by — there is no other projection channel,
    which structurally rules out 'non-grouped raw field alongside an aggregate'."""
    # Aggregate mode forbids row-mode projection/sorting.
    if not request.aggregations:
        errors.append(FieldError(
            ErrorCode.GROUP_BY_REQUIRES_AGGREGATION,
            "group_by requires at least one aggregation",
            "group_by", {"group_by": list(request.group_by)},
        ))
    if request.sort:
        errors.append(FieldError(
            ErrorCode.AGGREGATE_MODE_CONFLICT,
            "sorting aggregated results is not supported", "sort", {},
        ))
    if request.select:
        errors.append(FieldError(
            ErrorCode.AGGREGATE_MODE_CONFLICT,
            "select projection is not allowed with aggregations; aggregations define "
            "their own output (group keys + aggregate values)",
            "select", {"select": list(request.select)},
        ))

    output_names: set[str] = set()

    for i, agg in enumerate(request.aggregations):
        loc = f"aggregations[{i}]"
        fn = (agg.func or "").lower()
        if fn not in _AGG_FUNCS:
            errors.append(FieldError(
                ErrorCode.UNKNOWN_AGGREGATE_FUNC,
                f"unknown aggregate function {agg.func!r}; allowed: {sorted(_AGG_FUNCS)}",
                f"{loc}.func", {"func": agg.func, "allowed": sorted(_AGG_FUNCS)},
            ))
            continue

        is_count_star = fn == "count" and agg.field in (None, "*")
        resolved: Optional[_Resolved] = None
        if is_count_star:
            alias = "count"
        else:
            if agg.field in (None, "*"):
                errors.append(FieldError(
                    ErrorCode.AGGREGATE_FIELD_REQUIRED,
                    f"aggregate {fn!r} requires a field",
                    f"{loc}.field", {"func": fn},
                ))
                continue
            resolved = _resolve(index, agg.field, f"{loc}.field", errors)
            if resolved is None:
                continue
            ftype = resolved.field_def.type
            if fn in ("sum", "avg") and ftype not in _NUMERIC_TYPES:
                errors.append(FieldError(
                    ErrorCode.AGGREGATE_TYPE_MISMATCH,
                    f"{fn!r} requires a numeric field; {agg.field!r} is {ftype.value}",
                    f"{loc}.field", {"func": fn, "field": agg.field, "type": ftype.value},
                ))
                continue
            if fn in ("min", "max") and ftype not in _ORDERABLE_TYPES:
                errors.append(FieldError(
                    ErrorCode.AGGREGATE_TYPE_MISMATCH,
                    f"{fn!r} requires an orderable field; {agg.field!r} is {ftype.value}",
                    f"{loc}.field", {"func": fn, "field": agg.field, "type": ftype.value},
                ))
                continue
            alias = f"{fn}_{agg.field}"

        if alias in output_names:
            errors.append(FieldError(
                ErrorCode.DUPLICATE_OUTPUT_FIELD,
                f"duplicate aggregate output {alias!r}; give it a distinct func/field",
                loc, {"alias": alias},
            ))
            continue
        output_names.add(alias)
        plan_aggs.append((alias, fn, resolved))

    for i, name in enumerate(request.group_by):
        loc = f"group_by[{i}]"
        resolved = _resolve(index, name, loc, errors)
        if resolved is None:
            continue
        if name in output_names:
            errors.append(FieldError(
                ErrorCode.DUPLICATE_OUTPUT_FIELD,
                f"group key {name!r} collides with an aggregate output name",
                loc, {"field": name},
            ))
            continue
        output_names.add(name)
        plan_groups.append((name, resolved))


# ===========================================================================
# Build helpers
# ===========================================================================

def _collect_joins(
    plan_filters: list[_PlanFilter],
    plan_sorts: list[_PlanSort],
    selected: list[tuple[str, _Resolved]],
) -> tuple[str, ...]:
    """Ordered, de-duplicated set of join names needed by filters, sorts, and the
    explicit select — including joins a virtual resolver requires (e.g. pipeline_stage)."""
    ordered: dict[str, None] = {}

    def add(resolved: _Resolved) -> None:
        if resolved.virtual is not None:
            for jn in resolved.virtual.requires_joins:
                ordered.setdefault(jn, None)
        elif resolved.join is not None:
            ordered.setdefault(resolved.join.name, None)

    for pf in plan_filters:
        add(pf.resolved)
    for ps in plan_sorts:
        add(ps.resolved)
    for _name, res in selected:
        add(res)
    return tuple(ordered)


def _build_projection(
    entity: str,
    model: type,
    entity_def: EntityDef,
    joins_used: tuple[str, ...],
    ctx: ResolverContext,
    selected: list[tuple[str, _Resolved]],
) -> tuple[list, tuple[str, ...]]:
    """Build the labeled output columns.

    - ``select`` present -> output is EXACTLY the selected fields, in order (raw
      column / joined column / virtual expression), each labeled by its name.
    - ``select`` empty -> default: the entity's RAW fields from schema.py (never the
      whole model — so excluded columns can't leak), plus the exposed fields of any
      join in ``joins_used`` (joins referenced by a filter/sort), as before.
    """
    columns: list = []
    names: list[str] = []

    if selected:
        for name, resolved in selected:
            columns.append(_column(entity, model, resolved, ctx).label(name))
            names.append(name)
        return columns, tuple(names)

    for fd in entity_def.fields:
        columns.append(getattr(model, fd.name).label(fd.name))
        names.append(fd.name)

    join_by_name = {j.name: j for j in entity_def.joins}
    for join_name in joins_used:
        joindef = join_by_name[join_name]
        target_model, colmap = _JOIN_BINDINGS[(entity, join_name)]
        for fd in joindef.exposes:
            columns.append(getattr(target_model, colmap[fd.name]).label(fd.name))
            names.append(fd.name)

    return columns, tuple(names)


def _column(entity: str, model: type, resolved: _Resolved, ctx: ResolverContext):
    """Resolve a validated reference to its bound SQL expression (root column,
    joined column, or virtual-field expression)."""
    if resolved.virtual is not None:
        return resolved.virtual.build(ctx)
    if resolved.join is None:
        return getattr(model, resolved.field_def.name)
    target_model, colmap = _JOIN_BINDINGS[(entity, resolved.join.name)]
    return getattr(target_model, colmap[resolved.field_def.name])


def _predicate(column, op: Operator, value: Any):
    """Build a parameterized predicate. Values bind as parameters — never interpolated."""
    if op is Operator.EQ:
        return column == value
    if op is Operator.LT:
        return column < value
    if op is Operator.LTE:
        return column <= value
    if op is Operator.GT:
        return column > value
    if op is Operator.GTE:
        return column >= value
    if op is Operator.BETWEEN:
        return column.between(value[0], value[1])
    if op is Operator.IN:
        return column.in_(list(value))
    if op is Operator.CONTAINS:
        # Escape LIKE wildcards in the user value so a literal "%" or "_" matches
        # itself instead of acting as a wildcard. The escaped value still binds as
        # a parameter — the "%...%" wrapping is parameter content, not SQL text.
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return column.ilike(f"%{escaped}%", escape="\\")
    raise AssertionError(f"unhandled operator {op!r}")  # unreachable: ops are validated


def _apply_tenant_predicate(stmt, model: type, business_id: UUID):
    """Enforcement 1: inject the tenant predicate. Factored so multi-hop joins can
    reuse it on joined entities in a later step."""
    return stmt.where(getattr(model, _TENANT_COLUMN) == business_id)


def _apply_joins(stmt, entity: str, model: type, entity_def: EntityDef, joins_used: tuple[str, ...]):
    """LEFT OUTER JOIN each used join (single- or multi-hop), shared by row +
    aggregate builds. Joins are de-duplicated by target model, so a query that
    uses both the ``lead`` join (lead_title) and the two-hop ``customer`` join
    (which also traverses leads) joins the leads table only once."""
    join_by_name = {j.name: j for j in entity_def.joins}
    joined_models: set[type] = set()

    def do_join(stmt, target_model, onclause):
        if target_model in joined_models:
            return stmt  # already in FROM (one FK path per table in this schema)
        joined_models.add(target_model)
        return stmt.join(target_model, onclause, isouter=True)

    for join_name in joins_used:
        joindef = join_by_name[join_name]
        if joindef.through is not None:
            # Multi-hop: apply each hop's ON-clause in order (e.g. invoices -> leads -> customers).
            for target_model, onclause in _THROUGH_JOINS[(entity, join_name)]:
                stmt = do_join(stmt, target_model, onclause)
        else:
            target_model, _ = _JOIN_BINDINGS[(entity, join_name)]
            onclause = getattr(model, joindef.local_key) == getattr(target_model, joindef.target_key)
            stmt = do_join(stmt, target_model, onclause)
    return stmt


def _pagination(request: ReadQuery) -> tuple[int, int]:
    """Enforcement 3: hard row cap. Returns (offset, limit). A limit over the cap is
    clamped (not rejected); a missing limit gets DEFAULT_LIMIT. In aggregate mode this
    caps the number of GROUPS returned."""
    applied_offset = request.offset or 0
    applied_limit = min(request.limit if request.limit is not None else DEFAULT_LIMIT, HARD_ROW_CAP)
    return applied_offset, applied_limit


def _build_row_query(
    request: ReadQuery,
    entity_def: EntityDef,
    model: type,
    ctx: ResolverContext,
    plan_filters: list[_PlanFilter],
    plan_sorts: list[_PlanSort],
    selected: list[tuple[str, _Resolved]],
    business_id: UUID,
) -> CompiledQuery:
    """Build a normal entity-row query (projection + filters + sort + pagination)."""
    joins_used = _collect_joins(plan_filters, plan_sorts, selected)

    output_columns, output_fields = _build_projection(
        request.entity, model, entity_def, joins_used, ctx, selected,
    )
    stmt = select(*output_columns).select_from(model)
    stmt = _apply_joins(stmt, request.entity, model, entity_def, joins_used)
    stmt = _apply_tenant_predicate(stmt, model, business_id)

    for pf in plan_filters:
        stmt = stmt.where(_predicate(_column(request.entity, model, pf.resolved, ctx), pf.op, pf.value))
    for ps in plan_sorts:
        column = _column(request.entity, model, ps.resolved, ctx)
        stmt = stmt.order_by(column.desc() if ps.descending else column.asc())

    applied_offset, applied_limit = _pagination(request)
    stmt = stmt.offset(applied_offset).limit(applied_limit)

    return CompiledQuery(
        entity=request.entity,
        statement=stmt,
        output_fields=output_fields,
        applied_limit=applied_limit,
        applied_offset=applied_offset,
        joins_used=joins_used,
        business_id=business_id,
    )


def _build_aggregate_query(
    request: ReadQuery,
    entity_def: EntityDef,
    model: type,
    ctx: ResolverContext,
    plan_filters: list[_PlanFilter],
    plan_aggs: list[tuple[str, str, Optional[_Resolved]]],
    plan_groups: list[tuple[str, _Resolved]],
    business_id: UUID,
) -> CompiledQuery:
    """Build an aggregate query: SELECT group keys + aggregate values, WHERE
    (tenant + filters) before GROUP BY, capped by the hard row limit (on groups)."""
    # Joins needed by group keys, aggregate fields, and filters (incl. virtual deps).
    ordered: dict[str, None] = {}

    def _add(resolved: _Resolved) -> None:
        if resolved.virtual is not None:
            for jn in resolved.virtual.requires_joins:
                ordered.setdefault(jn, None)
        elif resolved.join is not None:
            ordered.setdefault(resolved.join.name, None)

    for pf in plan_filters:
        _add(pf.resolved)
    for _name, resolved in plan_groups:
        _add(resolved)
    for _alias, _fn, resolved in plan_aggs:
        if resolved is not None:
            _add(resolved)
    joins_used = tuple(ordered)

    select_cols: list = []
    group_exprs: list = []
    group_names: list[str] = []
    for name, resolved in plan_groups:
        expr = _column(request.entity, model, resolved, ctx)
        group_exprs.append(expr)
        group_names.append(name)
        select_cols.append(expr.label(name))

    aggregate_aliases: list[str] = []
    for alias, fn, resolved in plan_aggs:
        if resolved is None:           # count(*) — count rows
            agg_expr = func.count()
        else:
            agg_expr = _AGG_FUNCS[fn](_column(request.entity, model, resolved, ctx))
        select_cols.append(agg_expr.label(alias))
        aggregate_aliases.append(alias)

    stmt = select(*select_cols).select_from(model)
    stmt = _apply_joins(stmt, request.entity, model, entity_def, joins_used)
    # Enforcement 1: tenant predicate runs in WHERE — i.e. BEFORE GROUP BY.
    stmt = _apply_tenant_predicate(stmt, model, business_id)
    for pf in plan_filters:
        stmt = stmt.where(_predicate(_column(request.entity, model, pf.resolved, ctx), pf.op, pf.value))
    if group_exprs:
        stmt = stmt.group_by(*group_exprs)

    applied_offset, applied_limit = _pagination(request)
    stmt = stmt.offset(applied_offset).limit(applied_limit)

    return CompiledQuery(
        entity=request.entity,
        statement=stmt,
        output_fields=tuple(group_names + aggregate_aliases),
        applied_limit=applied_limit,
        applied_offset=applied_offset,
        joins_used=joins_used,
        business_id=business_id,
        is_aggregate=True,
        group_fields=tuple(group_names),
        aggregate_aliases=tuple(aggregate_aliases),
    )
