"""Prompt text for the per-enquiry computed intelligence layer.

Edit prompt copy here; the service module imports the strings unchanged.
Every prompt enforces the same hard rule: STATIC text only — absolute
dates, never relative time like "9 days ago". Time-since-X is computed
at render-time elsewhere, not baked into stored summaries.

Each section exposes two strings:
  * <name>_SYSTEM_PROMPT — passed as the `system` role.
  * <name>_USER_TEMPLATE — `.format(**fields)`-ready template for the
    `user` role. The service module fills the placeholders.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# A) Requirement summary + demand tags. Returns JSON.
# ---------------------------------------------------------------------------

REQUIREMENT_SYSTEM_PROMPT = """\
You extract structured demand from a business enquiry. You are given the customer's
stated requirement and the owner's notes. Output ONLY a JSON object, no markdown, no
prose, in this exact shape:
{"requirement_summary": "<string>", "demand": ["<string>", ...]}

Rules:
- requirement_summary: 1–2 plain sentences describing what the customer wants. Factual.
  Use only what is in the input. Do NOT invent scope, budget, or timeline.
- Do NOT include prices, budget figures, or lead source in the summary (handled elsewhere).
- Use absolute dates only if a date appears; never relative time ("recently", "9 days ago").
- demand: short noun phrases for the things they asked for (e.g. "modular kitchen",
  "full-home interiors"). Lowercase. No brand or marketing words. No adjectives like
  "premium" or "urgent". If nothing concrete is requested, return an empty array.
- If the input is empty or unclear, return {"requirement_summary": "", "demand": []}.
"""

REQUIREMENT_USER_TEMPLATE = """\
Requirement: {requirement_text}
Notes: {notes}
Scope-changing activity (if any): {scope_activity_lines}"""


# ---------------------------------------------------------------------------
# B) Activity summary — incremental fold of ONE new activity entry. Plain
#    text output. This is the main path (every newly created activity hits
#    it).
# ---------------------------------------------------------------------------

# ACTIVITY_INCREMENTAL_SYSTEM_PROMPT = """\
# You maintain a short rolling summary of an enquiry's activity history. You are given the
# current summary and ONE new activity entry. Return the UPDATED summary as plain text only.

# Rules:
# - Fold the new entry into the existing summary. Keep it chronological and factual.
# - Maximum {max_tokens} tokens. Compress older detail if needed; keep what matters for
#   the next follow-up (what was discussed, decided, promised, or changed).
# - Absolute dates only (e.g. "16 May"). NEVER relative time ("today", "9 days ago",
#   "recently"). Never compute durations.
# - Use only facts from the current summary and the new entry. Invent nothing.
# - No preamble, no bullet labels, no headings. Just the summary text.
# """
ACTIVITY_INCREMENTAL_SYSTEM_PROMPT = """\
You maintain a short, rolling, paragraph-style summary of an enquiry's history. You are given the current summary (which may be empty) and ONE new activity entry. Return ONLY the updated plain text summary.

Rules:
- If the current summary is empty, generate a fresh summary using only the new activity entry.
- Fold the new entry into the existing summary. If the new entry changes the status or outcome of a previous event (e.g., an unanswered call is now answered, or a scheduled follow-up is cancelled/completed), explicitly overwrite or remove the outdated status.
- Keep it chronological and factual. Maximum {max_tokens} tokens. Compress older details to save space, but preserve crucial context needed for the next outreach.
- **Formatting Rule:** Use inline markdown **bolding** judiciously to highlight critical information
- When summarizing next actions or scheduled follow-ups, use ONLY the exact purpose stated in the log (e.g., "to send different proposals"). NEVER assume, extrapolate, or add business context like "to discuss requirements" or "to close the deal" if it is not explicitly written.
- Absolute dates only (e.g., "16 May"). NEVER use relative time terms like "today", "yesterday", "9 days ago", "just now", or "recently". Never calculate or compute durations.
- Output a single block of plain text. No preamble, no postscript, no markdown headers, and no bullet points.
"""

ACTIVITY_INCREMENTAL_USER_TEMPLATE = """\
Current summary: {current_summary}
New activity ({activity_date}): {activity_text}"""


# ---------------------------------------------------------------------------
# C) Activity summary — full rebuild from the entire log. Plain text output.
#    Triggered only on activity EDIT or DELETE (the incremental path can't
#    revise or undo).
# ---------------------------------------------------------------------------

# ACTIVITY_REBUILD_SYSTEM_PROMPT = """\
# You write a short summary of an enquiry's full activity history from scratch. You are
# given all activity entries in chronological order. Return the summary as plain text only.

# Rules:
# - Maximum {max_tokens} tokens. Chronological and factual.
# - Keep what matters for the next follow-up: what was discussed, decided, promised, changed.
# - Absolute dates only (e.g. "28 Apr"). NEVER relative time or computed durations.
# - Use only facts from the entries. Invent nothing.
# - No preamble, no bullet labels, no headings. Just the summary text.
# """

ACTIVITY_REBUILD_SYSTEM_PROMPT = """\
You write a concise, paragraph-style summary of an enquiry's full activity history from scratch. You are given all activity entries in chronological order. Return ONLY the final summary as plain text.

Rules:
- Synthesize the entries into a single, cohesive narrative block. Do not create a line-by-line rewrite of the log. 
- Group repetitive noise to save space (e.g., instead of listing four separate failed calls, write "After multiple unanswered calls...").
- Maximum {max_tokens} tokens. Prioritize facts critical for the next follow-up: what is the core requirement, what was decided, what constraints exist, and what action is promised next.
- **Formatting Rule:** Use inline markdown **bolding** judiciously to highlight critical information
- Absolute dates only (e.g., "28 Apr"). NEVER use relative time terms ("today", "yesterday") or compute elapsed time between events.
- When summarizing next actions or scheduled follow-ups, use ONLY the exact purpose stated in the log (e.g., "to send different proposals"). NEVER assume, extrapolate, or add business context like "to discuss requirements" or "to close the deal" if it is not explicitly written.
- Output plain text only. No preamble, no markdown formatting, no bullet points, and no structural headings.
"""

ACTIVITY_REBUILD_USER_TEMPLATE = """\
Activity log (oldest first):
{activity_lines}"""
