"""Read-model compiler + executor tests.

The repo has no pytest dependency, so these are dependency-free: plain functions
with asserts, runnable directly with

    python -m app.read_model.tests.test_read_model

Each function is also named ``test_*`` so pytest will collect them if it is ever
added. No database is required — execution is tested against a fake session.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from app.read_model.compiler import (
    Aggregation,
    CompiledQuery,
    ErrorCode,
    Filter,
    Operator,
    ReadModelValidationError,
    ReadQuery,
    SortKey,
    _predicate,
    compile_query,
)
from app.read_model.executor import AggregateResult, QueryResult, execute_query, _to_jsonable

_BID = uuid4()
_NOW = datetime(2026, 5, 22, 10, 30, 0)


def _sql(cq) -> str:
    return " ".join(str(cq.statement).split())


def _codes(exc: ReadModelValidationError):
    return [e.code for e in exc.errors]


def _expect_reject(fn):
    try:
        fn()
    except ReadModelValidationError as exc:
        return exc
    raise AssertionError("expected ReadModelValidationError")


# ---------------------------------------------------------------------------
# Check 1 — unknown entity fails fast: exactly one error, no field validation.
# ---------------------------------------------------------------------------

def test_unknown_entity_fails_fast_with_single_error():
    # Field-level junk is included to prove it is NOT validated when the entity
    # is unknown — the compiler must bail with exactly one entity error.
    req = ReadQuery(
        entity="payments",
        filters=[Filter("bogus_field", "nonsense_op", [1, 2, 3])],
        sort=[SortKey("also_bogus", "sideways")],
        limit=-5,
    )
    try:
        compile_query(req, business_id=_BID)
    except ReadModelValidationError as exc:
        assert len(exc.errors) == 1, f"expected exactly one error, got {len(exc.errors)}"
        assert exc.errors[0].code is ErrorCode.UNKNOWN_ENTITY
        assert exc.errors[0].location == "entity"
    else:
        raise AssertionError("expected ReadModelValidationError for unknown entity")


# ---------------------------------------------------------------------------
# Check 2 — contains/ilike escapes %, _ and the escape char.
# ---------------------------------------------------------------------------

class _FakeColumn:
    """Captures ilike() arguments so we can assert on the bound value/escape."""

    def __init__(self):
        self.ilike_value = None
        self.ilike_escape = None

    def ilike(self, value, escape=None):
        self.ilike_value = value
        self.ilike_escape = escape
        return ("ilike", value, escape)


def test_contains_escapes_like_wildcards():
    col = _FakeColumn()
    _predicate(col, Operator.CONTAINS, "50% off_today\\now")
    # %, _ and \ must be backslash-escaped inside the bound value...
    assert col.ilike_value == "%50\\% off\\_today\\\\now%"
    # ...and the escape character must be declared to the DB.
    assert col.ilike_escape == "\\"


def test_contains_plain_value_unchanged():
    col = _FakeColumn()
    _predicate(col, Operator.CONTAINS, "kitchen")
    assert col.ilike_value == "%kitchen%"


# ---------------------------------------------------------------------------
# Output column restriction — excluded fields (leads.notes) never projected.
# ---------------------------------------------------------------------------

def test_projection_is_schema_fields_not_whole_model():
    cq = compile_query(ReadQuery("leads"), business_id=_BID)
    sql = " ".join(str(cq.statement).split())
    # Default projection is the schema's RAW field list — NOT every model column.
    # `updated_at` is on the model but excluded from schema -> must never be selected.
    assert "updated_at" not in cq.output_fields
    assert "leads.updated_at" not in sql, "non-schema column leads.updated_at must never be selected"
    # business_id is the tenant key — never projected; it is a WHERE predicate only.
    assert "business_id" not in cq.output_fields
    assert "leads.business_id = " in sql
    # `notes` is now an exposed schema field (founder decision) -> present by default.
    assert "notes" in cq.output_fields
    assert "id" in cq.output_fields and "title" in cq.output_fields and "estimated_value" in cq.output_fields


def test_joined_fields_projected_only_when_referenced():
    # No reference to the customer join -> no customer columns projected.
    no_join = compile_query(ReadQuery("leads"), business_id=_BID)
    assert "customer_name" not in no_join.output_fields
    assert "customer" not in no_join.joins_used

    # Filtering on a joined field pulls in the join AND its exposed columns.
    with_join = compile_query(
        ReadQuery("leads", filters=[Filter("customer_name", "contains", "raj")]),
        business_id=_BID,
    )
    assert "customer" in with_join.joins_used
    assert "customer_name" in with_join.output_fields
    assert "customer_phone" in with_join.output_fields  # all exposed fields of a used join


# ---------------------------------------------------------------------------
# Executor formatting (no DB) — JSON-safe coercion + strict key projection.
# ---------------------------------------------------------------------------

class _Status(str, Enum):
    DRAFT = "draft"


class _FakeMappingResult:
    def __init__(self, mappings):
        self._mappings = mappings

    def mappings(self):
        return self

    def all(self):
        return self._mappings


class _FakeSession:
    def __init__(self, mappings):
        self._mappings = mappings

    def execute(self, _statement):
        return _FakeMappingResult(self._mappings)


def test_executor_formats_jsonable_and_keys_by_output_fields():
    iid = uuid4()
    compiled = CompiledQuery(
        entity="invoices",
        statement=object(),  # ignored by the fake session
        output_fields=("id", "status", "due_date", "total_amount"),
        applied_limit=50,
        applied_offset=0,
        joins_used=(),
        business_id=_BID,
    )
    # The DB mapping includes an extra "notes" key; the executor must ignore it
    # (it keys strictly by output_fields).
    fake = _FakeSession([
        {
            "id": iid,
            "status": _Status.DRAFT,
            "due_date": date(2026, 3, 31),
            "total_amount": Decimal("12500.50"),
            "notes": "INTERNAL — must not appear",
        }
    ])
    result = execute_query(compiled, fake)
    row = result.rows[0]

    assert set(row.keys()) == {"id", "status", "due_date", "total_amount"}
    assert "notes" not in row
    assert row["id"] == str(iid)                  # UUID -> str
    assert row["status"] == "draft"               # Enum -> raw .value
    assert row["due_date"] == "2026-03-31"        # date -> isoformat
    assert row["total_amount"] == "12500.50"      # Decimal -> string (precision kept)
    assert result.returned == 1
    assert result.to_dict()["pagination"] == {"limit": 50, "offset": 0, "returned": 1}


def test_to_jsonable_scalars():
    assert _to_jsonable(None) is None
    assert _to_jsonable(True) is True
    assert _to_jsonable(7) == 7
    assert _to_jsonable("x") == "x"
    assert _to_jsonable(Decimal("0.10")) == "0.10"


# ---------------------------------------------------------------------------
# Virtual fields — balance and the aggregating sub-queries (canonical §1).
# ---------------------------------------------------------------------------

def test_balance_filter_builds_tenant_scoped_subqueries():
    cq = compile_query(
        ReadQuery("invoices", filters=[Filter("balance", ">", 0)], select=["balance"]),
        business_id=_BID,
    )
    sql = _sql(cq).lower()
    # payments sub-query: active-only + explicit business_id + correlated to invoice
    assert "sum(payments.amount)" in sql
    assert "payments.voided_at is null" in sql
    assert "payments.business_id =" in sql
    assert "payments.invoice_id = invoices.id" in sql
    # adjustments sub-query: no business_id column -> correlation only
    assert "sum(invoice_adjustments.amount)" in sql
    assert "invoice_adjustments.invoice_id = invoices.id" in sql
    # selected -> projected
    assert "balance" in cq.output_fields


def test_balance_filter_alone_does_not_project():
    # A virtual referenced ONLY in a filter is no longer auto-projected; output
    # defaults to the entity's raw schema fields.
    cq = compile_query(ReadQuery("invoices", filters=[Filter("balance", ">", 0)]), business_id=_BID)
    assert "balance" not in cq.output_fields
    assert "invoice_number" in cq.output_fields  # raw default still present


def test_balance_projected_via_select():
    cq = compile_query(ReadQuery("invoices", select=["balance"]), business_id=_BID)
    assert cq.output_fields == ("balance",)
    assert "sum(payments.amount)" in _sql(cq).lower()


# ---------------------------------------------------------------------------
# Time-relative virtual fields require an injected clock (canonical §5/§6/§11).
# ---------------------------------------------------------------------------

def test_is_overdue_without_now_raises_value_error():
    try:
        compile_query(ReadQuery("invoices", filters=[Filter("is_overdue", "=", True)]), business_id=_BID)
    except ValueError as e:
        assert "now" in str(e).lower()
    else:
        raise AssertionError("expected ValueError when 'now' missing for time-relative field")


def test_is_overdue_with_now_builds_canonical_predicate():
    cq = compile_query(
        ReadQuery("invoices", filters=[Filter("is_overdue", "=", True)], select=["is_overdue"]),
        business_id=_BID, now=_NOW,
    )
    sql = _sql(cq).lower()
    assert "invoices.due_date <" in sql
    assert "invoices.status not in" in sql
    assert "sum(payments.amount)" in sql      # balance reused inside is_overdue
    assert "is_overdue" in cq.output_fields


def test_days_overdue_filter_uses_case_and_is_null_safe():
    # days_overdue > 30: NULL rows (not overdue) excluded by SQL 3-valued logic.
    cq = compile_query(
        ReadQuery("invoices", filters=[Filter("days_overdue", ">", 30)], select=["days_overdue"]),
        business_id=_BID, now=_NOW,
    )
    sql = _sql(cq).lower()
    assert "case when" in sql
    assert "days_overdue" in cq.output_fields


def test_has_overdue_followup_builds_exists_subquery():
    cq = compile_query(
        ReadQuery("leads", filters=[Filter("has_overdue_followup", "=", True)]),
        business_id=_BID, now=_NOW,
    )
    sql = _sql(cq).lower()
    assert "exists" in sql
    assert "lead_followups.lead_id = leads.id" in sql
    assert "lead_followups.status =" in sql
    assert "lead_followups.scheduled_at <" in sql


# ---------------------------------------------------------------------------
# Pure-status virtual fields need no clock (canonical §3/§4).
# ---------------------------------------------------------------------------

def test_document_type_enum_filter_validates_values():
    ok = compile_query(
        ReadQuery("invoices", filters=[Filter("document_type", "=", "estimate")], select=["document_type"]),
        business_id=_BID,
    )
    assert "case when" in _sql(ok).lower()
    assert "document_type" in ok.output_fields

    exc = _expect_reject(lambda: compile_query(
        ReadQuery("invoices", filters=[Filter("document_type", "=", "invoice")]),
        business_id=_BID,
    ))
    assert ErrorCode.INVALID_ENUM_VALUE in _codes(exc)


def test_is_cancelled_needs_no_clock():
    cq = compile_query(
        ReadQuery("invoices", filters=[Filter("is_cancelled", "=", True)], select=["is_cancelled"]),
        business_id=_BID,  # no now
    )
    assert "is_cancelled" in cq.output_fields


# ---------------------------------------------------------------------------
# JOIN-dependent virtual fields pull in the pipeline_stage join (canonical §9/§10).
# ---------------------------------------------------------------------------

def test_stage_name_select_pulls_join_and_projects():
    cq = compile_query(
        ReadQuery("leads", select=["stage_name"]),
        business_id=_BID,
    )
    assert "pipeline_stage" in cq.joins_used
    assert "stage_name" in cq.output_fields
    assert "left outer join pipeline_stages" in _sql(cq).lower()


def test_is_terminal_coalesces_to_false():
    cq = compile_query(ReadQuery("leads", select=["is_terminal"]), business_id=_BID)
    sql = _sql(cq).lower()
    assert "coalesce" in sql and "lower(pipeline_stages.name) in" in sql
    assert "is_terminal" in cq.output_fields
    assert "pipeline_stage" in cq.joins_used


# ---------------------------------------------------------------------------
# select — raw / virtual / joined projection + validation.
# ---------------------------------------------------------------------------

def test_select_mixes_raw_virtual_joined():
    cq = compile_query(
        ReadQuery("leads", select=["title", "notes", "stage_name", "customer_name"]),
        business_id=_BID,
    )
    assert cq.output_fields == ("title", "notes", "stage_name", "customer_name")
    # virtual stage_name needs pipeline_stage; joined customer_name needs customer.
    assert "pipeline_stage" in cq.joins_used and "customer" in cq.joins_used


def test_select_with_joined_field_returns_it():
    cq = compile_query(ReadQuery("leads", select=["title", "customer_name"]), business_id=_BID)
    assert cq.output_fields == ("title", "customer_name")
    assert "customer" in cq.joins_used
    assert "left outer join customers" in _sql(cq).lower()


def test_select_unknown_field_rejected():
    exc = _expect_reject(lambda: compile_query(
        ReadQuery("leads", select=["not_a_field"]), business_id=_BID,
    ))
    assert ErrorCode.UNKNOWN_FIELD in _codes(exc)


def test_select_business_id_rejected():
    exc = _expect_reject(lambda: compile_query(
        ReadQuery("leads", select=["business_id"]), business_id=_BID,
    ))
    assert ErrorCode.BUSINESS_ID_NOT_QUERYABLE in _codes(exc)


def test_select_two_hop_customer_supported_on_invoices():
    # invoices->customer is multi-hop (invoice -> lead -> customer); now supported.
    cq = compile_query(
        ReadQuery("invoices", select=["invoice_number", "customer_name", "customer_phone"]),
        business_id=_BID, now=_NOW,
    )
    assert cq.output_fields == ("invoice_number", "customer_name", "customer_phone")
    assert "customer" in cq.joins_used
    sql = _sql(cq).lower()
    assert "left outer join leads on invoices.lead_id = leads.id" in sql
    assert "left outer join customers on leads.customer_id = customers.id" in sql


def test_select_lead_title_and_customer_dedupes_leads_join():
    # lead_title (invoice->lead) + customer_name (invoice->lead->customer) must join
    # the leads table only ONCE.
    cq = compile_query(
        ReadQuery("invoices", select=["lead_title", "customer_name"]),
        business_id=_BID, now=_NOW,
    )
    assert cq.output_fields == ("lead_title", "customer_name")
    sql = _sql(cq).lower()
    assert sql.count("join leads") == 1, sql
    assert sql.count("join customers") == 1, sql


def test_select_time_relative_virtual_needs_clock():
    try:
        compile_query(ReadQuery("invoices", select=["is_overdue"]), business_id=_BID)  # no now
    except ValueError as e:
        assert "now" in str(e).lower()
    else:
        raise AssertionError("expected ValueError: select includes a time-relative virtual but now=None")


def test_select_with_aggregations_rejected():
    exc = _expect_reject(lambda: compile_query(
        ReadQuery("invoices", select=["balance"], aggregations=[Aggregation("count")]),
        business_id=_BID,
    ))
    assert ErrorCode.AGGREGATE_MODE_CONFLICT in _codes(exc)


def test_virtual_operator_validation_boolean_only_eq():
    # is_overdue is boolean -> only "=" allowed; "between" rejected (before clock check).
    exc = _expect_reject(lambda: compile_query(
        ReadQuery("invoices", filters=[Filter("is_overdue", "between", [1, 2])]),
        business_id=_BID,  # no now needed: rejected at operator validation
    ))
    assert ErrorCode.DISALLOWED_OPERATOR in _codes(exc)


def test_two_hop_virtual_path_not_triggered():
    # None of the 10 virtual fields need the two-hop invoices->customer join;
    # confirm a virtual field that needs a single-hop join is accepted.
    cq = compile_query(ReadQuery("leads", select=["is_active"]), business_id=_BID)
    assert "is_active" in cq.output_fields


# ---------------------------------------------------------------------------
# Aggregation — SQL shape, output shape, and coherence rejections.
# ---------------------------------------------------------------------------

def test_aggregate_count_grouped_by_status_sql():
    cq = compile_query(
        ReadQuery("invoices", aggregations=[Aggregation("count")], group_by=["status"]),
        business_id=_BID,
    )
    assert cq.is_aggregate is True
    assert cq.output_fields == ("status", "count")
    assert cq.group_fields == ("status",) and cq.aggregate_aliases == ("count",)
    sql = _sql(cq).lower()
    assert "count(*)" in sql
    assert "group by" in sql
    assert "invoices.business_id =" in sql  # tenant predicate (in WHERE, before GROUP BY)


def test_aggregate_sum_balance_grouped_sql():
    cq = compile_query(
        ReadQuery("invoices", aggregations=[Aggregation("sum", "balance")], group_by=["status"]),
        business_id=_BID,
    )
    assert cq.output_fields == ("status", "sum_balance")
    sql = _sql(cq).lower()
    assert "sum(" in sql
    assert "sum(payments.amount)" in sql            # balance virtual expanded inside the sum
    assert "group by" in sql


def test_aggregate_total_no_group_by():
    cq = compile_query(
        ReadQuery("invoices", aggregations=[Aggregation("sum", "total_amount")]),
        business_id=_BID,
    )
    assert cq.is_aggregate is True
    assert cq.group_fields == () and cq.output_fields == ("sum_total_amount",)
    assert "group by" not in _sql(cq).lower()       # single total row, no GROUP BY


def test_aggregate_output_shape_is_distinct():
    cq = compile_query(
        ReadQuery("invoices", aggregations=[Aggregation("count")], group_by=["status"]),
        business_id=_BID,
    )
    fake = _FakeSession([
        {"status": "approved", "count": 3},
        {"status": "draft", "count": 1},
    ])
    result = execute_query(cq, fake)
    assert isinstance(result, AggregateResult)
    d = result.to_dict()
    assert d["kind"] == "aggregate"
    assert d["group_by"] == ["status"] and d["aggregates"] == ["count"]
    assert d["rows"][0] == {"status": "approved", "count": 3}
    assert d["pagination"]["returned"] == 2


def test_row_query_kind_is_rows():
    cq = compile_query(ReadQuery("invoices"), business_id=_BID)
    assert isinstance(execute_query(cq, _FakeSession([])), QueryResult)
    assert execute_query(cq, _FakeSession([])).to_dict()["kind"] == "rows"


def test_aggregate_sum_on_non_numeric_rejected():
    exc = _expect_reject(lambda: compile_query(
        ReadQuery("invoices", aggregations=[Aggregation("sum", "status")]),  # status is enum
        business_id=_BID,
    ))
    assert ErrorCode.AGGREGATE_TYPE_MISMATCH in _codes(exc)


def test_group_by_without_aggregation_rejected():
    exc = _expect_reject(lambda: compile_query(
        ReadQuery("invoices", group_by=["status"]),  # no aggregations
        business_id=_BID,
    ))
    assert ErrorCode.GROUP_BY_REQUIRES_AGGREGATION in _codes(exc)


def test_aggregate_unknown_func_and_missing_field():
    exc = _expect_reject(lambda: compile_query(
        ReadQuery("invoices", aggregations=[Aggregation("median", "total_amount"), Aggregation("sum")]),
        business_id=_BID,
    ))
    codes = _codes(exc)
    assert ErrorCode.UNKNOWN_AGGREGATE_FUNC in codes      # "median"
    assert ErrorCode.AGGREGATE_FIELD_REQUIRED in codes    # sum with no field


def test_aggregate_count_needs_no_clock_but_is_overdue_filter_does():
    # count grouped by status: no time-relative field -> no 'now' required.
    compile_query(ReadQuery("invoices", aggregations=[Aggregation("count")], group_by=["status"]), business_id=_BID)
    # count(*) WHERE is_overdue -> time-relative -> requires 'now'.
    try:
        compile_query(
            ReadQuery("invoices", aggregations=[Aggregation("count")], filters=[Filter("is_overdue", "=", True)]),
            business_id=_BID,  # no now
        )
    except ValueError as e:
        assert "now" in str(e).lower()
    else:
        raise AssertionError("expected ValueError: is_overdue filter needs a clock even in aggregate mode")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001 - surface any unexpected error
            failures += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run())
