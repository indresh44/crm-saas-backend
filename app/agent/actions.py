"""Action types the LLM may emit + envelope parsing + identity signature."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal


ActionType = Literal["read", "prepare", "ask_user", "done"]

_REQUIRED_KEYS_PER_TYPE: dict[str, tuple[str, ...]] = {
    "read":     ("query",),
    "prepare":  ("capability", "inputs"),
    "ask_user": ("question",),
    "done":     ("answer",),
}


@dataclass(frozen=True)
class Action:
    type: ActionType
    raw: dict          # the full action dict from the LLM — preserved verbatim for replay/equality.


class ActionParseError(Exception):
    """Raised when the LLM JSON doesn't conform to the action envelope shape."""


def parse_action_json(obj: dict) -> tuple[str, Action]:
    """Parse the `{thought, action: {...}}` envelope. Returns (thought, Action)."""
    if not isinstance(obj, dict):
        raise ActionParseError(f"expected JSON object, got {type(obj).__name__}")
    thought = obj.get("thought", "")
    if not isinstance(thought, str):
        raise ActionParseError("'thought' must be a string")
    action = obj.get("action")
    if not isinstance(action, dict):
        raise ActionParseError("envelope must contain an 'action' object")
    atype = action.get("type")
    if atype not in _REQUIRED_KEYS_PER_TYPE:
        raise ActionParseError(
            f"action.type must be one of {list(_REQUIRED_KEYS_PER_TYPE)}; got {atype!r}"
        )
    for key in _REQUIRED_KEYS_PER_TYPE[atype]:
        if key not in action:
            raise ActionParseError(f"action.type={atype!r} requires '{key}'")
    return thought, Action(type=atype, raw=action)


def action_signature(action: Action) -> str:
    """Canonical JSON of the action dict. Two consecutive identical signatures
    trigger the loop's hard-stop (per spec — runaway-LLM guard)."""
    return json.dumps(action.raw, sort_keys=True, default=str)
