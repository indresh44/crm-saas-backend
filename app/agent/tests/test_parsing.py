"""Unit tests for parse_read_query — alias handling + teaching errors.
No DB required; runnable with `python -m app.agent.tests.test_parsing`."""

from __future__ import annotations

from app.agent.parsing import ReadQueryParseError, parse_read_query


# ---------------------------------------------------------------------------
# Alias: 'operator' is accepted as 'op' (the LLM mistake we observed live)
# ---------------------------------------------------------------------------

def test_filter_operator_alias_accepted():
    rq = parse_read_query({
        "entity": "leads",
        "filters": [{"field": "title", "operator": "contains", "value": "kitchen"}],
    })
    assert rq.filters[0].op == "contains"
    assert rq.filters[0].field == "title"
    assert rq.filters[0].value == "kitchen"


def test_filter_op_wins_when_both_op_and_operator_given():
    rq = parse_read_query({
        "entity": "leads",
        "filters": [{"field": "title", "op": "=", "operator": "contains", "value": "x"}],
    })
    assert rq.filters[0].op == "="     # canonical wins; alias silently dropped


def test_aggregation_function_alias_accepted():
    rq = parse_read_query({
        "entity": "leads",
        "aggregations": [{"function": "count"}],
    })
    assert rq.aggregations[0].func == "count"


# ---------------------------------------------------------------------------
# Teaching errors: unknown key is named, suggestion + allowed list included
# ---------------------------------------------------------------------------

def test_filter_unknown_key_errors_with_name_and_allowed_list():
    try:
        parse_read_query({
            "entity": "leads",
            "filters": [{"field": "title", "op": "=", "value": "x", "wrongkey": 1}],
        })
    except ReadQueryParseError as exc:
        assert exc.code == "unknown_key"
        assert "wrongkey" in exc.message
        assert "filters[0]" in exc.message
        assert "Allowed keys: ['field', 'op', 'value']" in exc.message
        assert exc.details["key"] == "wrongkey"
        assert exc.details["allowed"] == ["field", "op", "value"]
    else:
        raise AssertionError("expected ReadQueryParseError")


def test_filter_close_match_suggested():
    try:
        parse_read_query({
            "entity": "leads",
            "filters": [{"feild": "title", "op": "=", "value": "x"}],   # typo
        })
    except ReadQueryParseError as exc:
        assert "Did you mean 'field'" in exc.message
    else:
        raise AssertionError("expected suggestion for typo'd 'feild'")


def test_filter_missing_op_errors_with_clear_message():
    try:
        parse_read_query({
            "entity": "leads",
            "filters": [{"field": "title", "value": "x"}],   # no op, no operator
        })
    except ReadQueryParseError as exc:
        assert exc.code == "missing_filter_op"
        assert "'op'" in exc.message
        assert "'operator' is accepted" in exc.message   # tell the LLM the alias exists
    else:
        raise AssertionError("expected missing_filter_op error")


def test_filter_missing_field_errors():
    try:
        parse_read_query({"entity": "leads", "filters": [{"op": "=", "value": "x"}]})
    except ReadQueryParseError as exc:
        assert exc.code == "missing_filter_field"
        assert "'field'" in exc.message
    else:
        raise AssertionError("expected missing_filter_field error")


def test_sort_unknown_key_errors_clearly():
    try:
        parse_read_query({
            "entity": "leads",
            "sort": [{"field": "created_at", "order": "asc"}],   # 'order' not allowed
        })
    except ReadQueryParseError as exc:
        assert exc.code == "unknown_key"
        assert "'order'" in exc.message
        assert "Allowed keys: ['field', 'direction']" in exc.message
    else:
        raise AssertionError("expected unknown_key for sort 'order'")


def test_aggregation_unknown_key_errors():
    try:
        parse_read_query({
            "entity": "leads",
            "aggregations": [{"func": "count", "fild": "id"}],   # typo: 'fild'
        })
    except ReadQueryParseError as exc:
        assert exc.code == "unknown_key"
        assert "'fild'" in exc.message
        assert "Did you mean 'field'" in exc.message
    else:
        raise AssertionError("expected unknown_key error with suggestion")


def test_non_dict_filter_errors_clearly():
    try:
        parse_read_query({"entity": "leads", "filters": ["not a dict"]})
    except ReadQueryParseError as exc:
        assert exc.code == "bad_filter"
        assert "filters[0]" in exc.message
    else:
        raise AssertionError("expected bad_filter error")


# ---------------------------------------------------------------------------
# Happy path still works after refactor
# ---------------------------------------------------------------------------

def test_normal_read_still_parses():
    rq = parse_read_query({
        "entity": "invoices",
        "filters": [{"field": "status", "op": "=", "value": "approved"}],
        "sort": [{"field": "issued_date", "direction": "desc"}],
        "limit": 25,
        "select": ["invoice_number", "total_amount"],
        "aggregations": [],
        "group_by": [],
    })
    assert rq.entity == "invoices"
    assert rq.filters[0].field == "status"
    assert rq.sort[0].direction == "desc"
    assert rq.limit == 25


def test_top_level_extra_key_rejected_with_teaching_error():
    """CONTRACT FLIP (was test_top_level_extra_key_silently_ignored):

    Top-level lenience caused a silent dataset leak — the LLM emitted
    `where: {…}` instead of `filters: [...]`, the unknown `where` was
    silently dropped, and the resulting query had ZERO filters and
    returned the entire table. The fix made top-level strict; this test
    documents the new behaviour and the bug-A history is in the comment
    on _TOP_LEVEL_KEYS in app/agent/parsing.py."""
    try:
        parse_read_query({"entity": "leads", "kind": "read"})
    except ReadQueryParseError as exc:
        assert exc.code == "unknown_key"
        assert "'kind'" in exc.message
        # The error must list the allowed keys so the LLM can self-correct.
        for k in ("entity", "filters", "sort", "limit", "select"):
            assert k in exc.message, f"allowed key {k!r} missing from teaching error: {exc.message}"
    else:
        raise AssertionError("expected ReadQueryParseError(unknown_key)")


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
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run())
