"""Tests for the task splitter — heuristics, clamp, fallback behaviour.

No DB needed; uses a fake LLM. Run:
    python -m app.agent.tests.test_task_splitter
"""

from __future__ import annotations

import asyncio
import json

from app.agent.task_splitter import (
    MAX_TASKS,
    _clamp_with_remainder,
    split_into_tasks,
)
from app.services.llm_service import LLMError, LLMResponse


# ---------------------------------------------------------------------------
# Fake LLM
# ---------------------------------------------------------------------------

class _FakeLLM:
    def __init__(self, *responses):
        self._queue = list(responses)
        self.calls = 0

    async def chat(self, system_prompt: str, messages, **kwargs):
        self.calls += 1
        assert self._queue, "FakeLLM ran out of canned responses"
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        content = item if isinstance(item, str) else json.dumps(item)
        return LLMResponse(content=content, tool_calls=None, stop_reason="stop",
                           input_tokens=10, output_tokens=20, model="fake-llm")


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ===========================================================================
# Heuristic gates — these MUST NOT call the LLM
# ===========================================================================

def test_empty_string_returns_single_no_llm():
    llm = _FakeLLM()
    out = _run(split_into_tasks("", llm=llm))
    assert out == [""]
    assert llm.calls == 0


def test_whitespace_only_returns_single_no_llm():
    llm = _FakeLLM()
    out = _run(split_into_tasks("   \n\t  ", llm=llm))
    # The literal input is returned (preserves what the caller passed in).
    assert out == ["   \n\t  "]
    assert llm.calls == 0


def test_short_message_below_30_chars_skips_llm():
    llm = _FakeLLM()
    out = _run(split_into_tasks("hi and bye", llm=llm))
    assert out == ["hi and bye"]
    assert llm.calls == 0


def test_long_message_without_conjunctions_skips_llm():
    """Even a long message skips the LLM if no conjunction word is present.
    'show me todays followups please' has no and/plus/also/then/aur/etc."""
    msg = "show me todays followups please for my pipeline view"
    llm = _FakeLLM()
    out = _run(split_into_tasks(msg, llm=llm))
    assert out == [msg]
    assert llm.calls == 0


def test_conjunction_in_word_not_triggering():
    """'android' contains 'and' as a substring but the regex is word-bounded."""
    msg = "list all android related leads from my pipeline view please"
    llm = _FakeLLM()
    out = _run(split_into_tasks(msg, llm=llm))
    assert out == [msg]
    assert llm.calls == 0


# ===========================================================================
# LLM-driven splitting
# ===========================================================================

def test_llm_says_single_task_returns_single():
    msg = "find overdue invoices and send reminders to those clients"
    llm = _FakeLLM({"tasks": [msg]})
    out = _run(split_into_tasks(msg, llm=llm))
    assert out == [msg]
    assert llm.calls == 1


def test_llm_says_multiple_tasks_returns_them_in_order():
    msg = "show me todays followups and check any new enquiries please"
    llm = _FakeLLM({"tasks": [
        "show me todays followups",
        "check any new enquiries please",
    ]})
    out = _run(split_into_tasks(msg, llm=llm))
    assert out == [
        "show me todays followups",
        "check any new enquiries please",
    ]


def test_llm_strips_empty_and_non_string_entries():
    msg = "list customers and pending invoices and overdue payments please"
    llm = _FakeLLM({"tasks": [
        "list customers",
        "",                   # dropped
        None,                 # dropped (non-string)
        "  ",                 # dropped (whitespace-only)
        "list pending invoices",
        12345,                # dropped (non-string)
    ]})
    out = _run(split_into_tasks(msg, llm=llm))
    assert out == ["list customers", "list pending invoices"]


# ===========================================================================
# Bailout — invalid LLM output never crashes the chat
# ===========================================================================

def test_llm_error_falls_back_to_single():
    msg = "show overdue invoices and remind those clients please now"
    llm = _FakeLLM(LLMError("network down"))
    out = _run(split_into_tasks(msg, llm=llm))
    assert out == [msg]


def test_llm_returns_non_json_falls_back_to_single():
    msg = "show overdue invoices and remind those clients please now"
    llm = _FakeLLM("this is not json at all")
    out = _run(split_into_tasks(msg, llm=llm))
    assert out == [msg]


def test_llm_returns_empty_tasks_falls_back_to_single():
    msg = "show overdue invoices and remind those clients please now"
    llm = _FakeLLM({"tasks": []})
    out = _run(split_into_tasks(msg, llm=llm))
    assert out == [msg]


def test_llm_returns_missing_tasks_key_falls_back_to_single():
    msg = "show overdue invoices and remind those clients please now"
    llm = _FakeLLM({"items": ["a", "b"]})
    out = _run(split_into_tasks(msg, llm=llm))
    assert out == [msg]


def test_llm_returns_empty_content_falls_back_to_single():
    msg = "show overdue invoices and remind those clients please now"
    llm = _FakeLLM("")
    out = _run(split_into_tasks(msg, llm=llm))
    assert out == [msg]


# ===========================================================================
# Clamp — never silently drop an instruction
# ===========================================================================

def test_clamp_under_cap_unchanged():
    assert _clamp_with_remainder(["a", "b", "c"], 4) == ["a", "b", "c"]
    assert _clamp_with_remainder(["a", "b", "c", "d"], 4) == ["a", "b", "c", "d"]


def test_clamp_at_cap_unchanged():
    assert _clamp_with_remainder(["a", "b", "c", "d"], 4) == ["a", "b", "c", "d"]


def test_clamp_over_cap_merges_remainder_into_last_slot():
    out = _clamp_with_remainder(["a", "b", "c", "d", "e"], 4)
    assert len(out) == 4
    assert out[:3] == ["a", "b", "c"]
    # The 4th task carries the remainder so nothing is silently dropped.
    assert "d" in out[3] and "e" in out[3]
    assert "and" in out[3]   # join marker is visible


def test_clamp_far_over_cap_still_keeps_everything():
    out = _clamp_with_remainder(list("abcdefgh"), 4)
    assert len(out) == 4
    assert out[:3] == ["a", "b", "c"]
    for letter in "defgh":
        assert letter in out[3], f"letter {letter!r} silently dropped"


def test_splitter_clamps_llm_output_to_max_tasks():
    msg = ("show overdue invoices and remind clients and create a new lead "
           "and update catalog rates and send weekly report")
    llm = _FakeLLM({"tasks": [
        "show overdue invoices",
        "remind clients",
        "create a new lead",
        "update catalog rates",
        "send weekly report",
    ]})
    out = _run(split_into_tasks(msg, llm=llm))
    assert len(out) == MAX_TASKS == 4
    # The 4th task carries the 5th instruction.
    assert "update catalog rates" in out[3]
    assert "send weekly report" in out[3]


# ===========================================================================
# Driver
# ===========================================================================

def _all_tests():
    tests = [
        test_empty_string_returns_single_no_llm,
        test_whitespace_only_returns_single_no_llm,
        test_short_message_below_30_chars_skips_llm,
        test_long_message_without_conjunctions_skips_llm,
        test_conjunction_in_word_not_triggering,
        test_llm_says_single_task_returns_single,
        test_llm_says_multiple_tasks_returns_them_in_order,
        test_llm_strips_empty_and_non_string_entries,
        test_llm_error_falls_back_to_single,
        test_llm_returns_non_json_falls_back_to_single,
        test_llm_returns_empty_tasks_falls_back_to_single,
        test_llm_returns_missing_tasks_key_falls_back_to_single,
        test_llm_returns_empty_content_falls_back_to_single,
        test_clamp_under_cap_unchanged,
        test_clamp_at_cap_unchanged,
        test_clamp_over_cap_merges_remainder_into_last_slot,
        test_clamp_far_over_cap_still_keeps_everything,
        test_splitter_clamps_llm_output_to_max_tasks,
    ]
    failures = []
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures.append((t.__name__, exc))
            print(f"  FAIL {t.__name__}: {type(exc).__name__}: {exc}")
    print()
    print(f"{len(tests) - len(failures)}/{len(tests)} passed")
    return failures


if __name__ == "__main__":
    fails = _all_tests()
    raise SystemExit(1 if fails else 0)
