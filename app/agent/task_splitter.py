"""Owner message -> list of task descriptions.

One LLM call, default ONE task. Splitting only happens when the message
contains clearly independent instructions. The hard bias is under-splitting:
a single task with internal steps ("find overdue invoices AND send reminders")
is ONE task — the loop already chains read -> write internally. Splitting is
ONLY for genuinely independent tasks ("show today's follow-ups" + "check new
enquiries").

Three cheap guards run BEFORE the LLM is called:
  1. Empty / whitespace-only message  -> [message]
  2. Short message (< 30 chars)        -> [message]
  3. No splitting conjunctions present -> [message]

Only when at least one conjunction is found do we pay the roundtrip. The LLM
is asked for a strict JSON object {"tasks": [...]}. Any parse failure or
empty result -> fall back to [message]. The splitter must never block the
chat — if it can't decide, default to a single task.

Output is also clamped to AT MOST 4 tasks. If the LLM emits more, the 4th
task absorbs the remaining ones (joined by " — and — ") so we never silently
drop an instruction the owner gave.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from app.agent.parsing import extract_json_object
from app.services.llm_service import LLMError, LLMService


logger = logging.getLogger(__name__)


MAX_TASKS = 4
MIN_LEN_FOR_LLM = 30


# Cheap heuristic. English + Hinglish/Hindi conjunctions that frequently mark
# the join between two independent instructions. Bias is INCLUSIVE — false
# positives cost a splitter call (acceptable); false negatives skip splitting
# entirely (not acceptable for messages that genuinely have two tasks).
# Word-bounded so "android" doesn't trigger "and".
_CONJUNCTION_RE = re.compile(
    r"\b(and|plus|also|then|aur|aur\s+phir|saath\s+mein|saath)\b",
    re.IGNORECASE,
)


# The splitter is INTENTIONALLY pinned to a single fast model. Promoting it to
# the Pro tier would double the per-message latency for a job that doesn't
# need reasoning depth. Override via env if needed.
_SPLITTER_MODEL = "gemini/gemini-3.5-flash"


_SYSTEM_PROMPT = """You split a single chat message from a small-business owner
into a list of independent tasks.

The owner uses this assistant to manage their CRM (leads, customers, invoices,
payments, follow-ups). The assistant runs each task through a multi-step loop
that ALREADY chains reads and writes internally.

Your only job: decide whether ONE message contains TWO OR MORE genuinely
independent instructions, and if so, split them. Default to ONE task.

CRITICAL DISTINCTION — read this twice:
  * A single task with internal steps is ONE task, not many. The loop chains
    read -> write internally. Examples that are ONE task:
      - "Find overdue invoices and send reminders"
      - "Mark today's follow-ups done with note 'spoke to client'"
      - "Show Rajesh's invoices and the one from last week, then mark it paid"
      - "Look up the catalog item for modular kitchen and update its rate"
      - "Mujhe Rajesh ka follow-up update karna hai aur note bhi save karna hai"
        (Hinglish: same lead, one follow-up, internal note step — ONE task)

  * Independent tasks operate on DIFFERENT objects and produce DIFFERENT
    answers. Examples that are TWO OR MORE tasks:
      - "Show today's follow-ups and check any new enquiries"
        -> ["show today's follow-ups", "check any new enquiries"]
      - "Send a reminder to Rajesh for invoice INV-0042 and also create a new
         enquiry for Sharma kitchen project"
        -> ["send a reminder to Rajesh for invoice INV-0042",
            "create a new enquiry for Sharma kitchen project"]
      - "Pending payments dikha do aur ek naya lead bhi create karo for Anjali"
        -> ["pending payments dikha do",
            "ek naya lead create karo for Anjali"]

Under-splitting is correct when in doubt. Over-splitting fragments a request
and is a real failure. Return ONE task unless the message contains two or
more clearly independent instructions that operate on different objects or
produce different answers.

OUTPUT — strict JSON, nothing else:
  {"tasks": ["...", "..."]}

Single-task answers as a one-element array: {"tasks": ["the whole message"]}.
Each task string should be a self-contained instruction the assistant can act
on without seeing the others. Preserve the owner's original wording where you
can — don't paraphrase aggressively, don't translate.
"""


async def split_into_tasks(
    message: str,
    *,
    llm: Optional[LLMService] = None,
) -> list[str]:
    """Return >=1 task descriptions. Default is [message]. Never raises —
    any error or weird output bails to [message]."""
    text = (message or "").strip()
    if not text:
        return [message]

    # Heuristic gates — most messages never call the LLM.
    if len(text) < MIN_LEN_FOR_LLM:
        return [message]
    if not _CONJUNCTION_RE.search(text):
        return [message]

    llm = llm or LLMService()
    try:
        resp = await llm.chat(
            system_prompt=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
            model=_SPLITTER_MODEL,
        )
    except LLMError as exc:
        logger.warning("splitter LLM call failed; defaulting to single task: %s", exc)
        return [message]

    raw = (resp.content or "").strip()
    if not raw:
        logger.warning("splitter returned empty content; defaulting to single task")
        return [message]

    try:
        # extract_json_object already returns a parsed dict — no second
        # json.loads needed. It raises ValueError if no JSON object was found.
        envelope = extract_json_object(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "splitter returned non-JSON; defaulting to single task: %s | raw=%r",
            exc, raw[:200],
        )
        return [message]
    if not isinstance(envelope, dict):
        logger.warning(
            "splitter returned JSON but not an object; defaulting: %r", envelope,
        )
        return [message]

    tasks = envelope.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        logger.warning(
            "splitter envelope missing/empty 'tasks'; defaulting: %r", envelope,
        )
        return [message]

    cleaned: list[str] = []
    for entry in tasks:
        if not isinstance(entry, str):
            continue
        s = entry.strip()
        if s:
            cleaned.append(s)
    if not cleaned:
        return [message]

    return _clamp_with_remainder(cleaned, MAX_TASKS)


def _clamp_with_remainder(tasks: list[str], cap: int) -> list[str]:
    """Cap the list at `cap` items. If the LLM emitted more, the LAST kept
    entry absorbs everything beyond the cap, joined by ' — and — '. The
    explicit join marker (a) is recognisable in render so the owner sees why
    one task block contains multiple things, and (b) gives the downstream
    loop one cohesive goal string to act on. We never silently drop input."""
    if cap < 1:
        # Defensive — should never be reached with MAX_TASKS=4.
        return [" — and — ".join(tasks)]
    if len(tasks) <= cap:
        return tasks
    kept = tasks[: cap - 1]
    remainder = tasks[cap - 1 :]
    merged = " — and — ".join(remainder)
    return [*kept, merged]
