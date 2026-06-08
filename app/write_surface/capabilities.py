"""Capability declaration types for the write surface.

Pure type definitions — no logic. A capability declaration says:
  - what inputs the write takes (name, type, required, locked/editable, enum values)
  - how to validate + build a preview (read-only)
  - how to execute the underlying service call
  - how to re-check between commit and execute (scaffold today; real later)

The engine reads these declarations to drive the generic prepare/commit flow.
Adding a new write means: writing one declaration + adding it to the registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

from sqlmodel import Session

from app.models.user import User


# --- Hook signatures (explicit `session` so hooks can use the read model + services) ---

ValidateHook = Callable[[Session, User, dict[str, Any]], str]   # returns preview text
ExecuteHook = Callable[[Session, User, dict[str, Any]], dict[str, Any]]   # returns result
RecheckHook = Callable[[Session, User, dict[str, Any]], None]   # raises ValueError to abort


def _noop_recheck(_session: Session, _user: User, _inputs: dict[str, Any]) -> None:
    """Scaffold. The engine calls recheck() uniformly so adding real per-capability
    re-validation later is one-line per capability.

    TODO: when commit-time re-checks are needed (e.g. "is the invoice STILL not
    cancelled?", "did the balance change since prepare?"), populate this per
    capability. Today, between prepare and commit, the only protection is the
    15-minute TTL + the single-use atomic claim.
    """
    return None


@dataclass(frozen=True)
class InputSpec:
    name: str
    type: Literal["uuid", "decimal", "integer", "date", "datetime", "string", "boolean", "enum"]
    required: bool
    locked: bool = True
    enum_values: Optional[tuple[str, ...]] = None

    def __post_init__(self) -> None:
        if self.type == "enum" and not self.enum_values:
            raise ValueError(f"enum input {self.name!r} must declare enum_values")
        if self.type != "enum" and self.enum_values:
            raise ValueError(f"non-enum input {self.name!r} must not declare enum_values")


@dataclass(frozen=True)
class CapabilityDeclaration:
    name: str
    description: str
    inputs: tuple[InputSpec, ...]
    validate: ValidateHook
    execute: ExecuteHook
    recheck: RecheckHook = _noop_recheck
    risk_class: str = "confirm"   # layer 4 may override later
