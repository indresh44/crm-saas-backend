"""Write-surface engine: generic prepare + commit driven by capability declarations.

This module is the schema gate (input validation against declarations) and the
adapter between the prepared-action store and per-capability hooks. It knows
NOTHING about any specific write — capabilities live in caps/ and the registry.

Critical design rules:
  1. business_id is taken from current_user, never from inputs.
  2. The store's four error subclasses (PreparedActionNotFound etc.) collapse
     to ONE opaque PREPARED_ACTION_UNAVAILABLE here. The specific subclass is
     logged for ops, never surfaced to the caller.
  3. If execute() raises, the prepared action is already consumed (atomic claim
     happened first). The engine returns EXECUTE_FAILED with a clean message;
     the user must prepare again. NOT a 500. This trade-off is intentional.
  4. The store stays an opaque bag; the engine is the schema gate at BOTH
     prepare (full input set) AND commit (edited_fields must be a subset of
     editable input names, and re-validated against the same type rules).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from sqlmodel import Session

from app.models.user import User
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec
from app.write_surface.prepared_actions import (
    PreparedAction,
    PreparedActionError,
    consume_prepared_action,
    create_prepared_action,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public errors
# ---------------------------------------------------------------------------

class ErrorCode(str, Enum):
    UNKNOWN_CAPABILITY = "unknown_capability"
    INVALID_INPUT = "invalid_input"
    PREPARED_ACTION_UNAVAILABLE = "prepared_action_unavailable"
    EXECUTE_FAILED = "execute_failed"


class WriteSurfaceError(Exception):
    """Single exception type at the engine boundary. Distinct ErrorCode per
    failure mode; opaque to the caller (no stack, no underlying classes)."""

    def __init__(self, code: ErrorCode, message: str, *, details: Optional[dict[str, Any]] = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"[{code.value}] {message}")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class PreparedActionHandle:
    id: str
    preview: str
    editable_fields: tuple[str, ...]


# ---------------------------------------------------------------------------
# Input validation (schema gate)
# ---------------------------------------------------------------------------

def _coerce(spec: InputSpec, value: Any) -> Any:
    """Coerce a single value to the declared type. Raises ValueError on failure."""
    if value is None:
        return None
    t = spec.type
    if t == "uuid":
        return value if isinstance(value, UUID) else UUID(str(value))
    if t == "decimal":
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(str(exc))
    if t == "integer":
        if isinstance(value, bool):
            raise ValueError("bool not accepted for integer")
        return int(value)
    if t == "date":
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))
    if t == "datetime":
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if t == "string":
        return str(value)
    if t == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)
    if t == "enum":
        s = str(value)
        if s not in (spec.enum_values or ()):
            raise ValueError(f"{s!r} not in allowed values {list(spec.enum_values or ())}")
        return s
    raise ValueError(f"unknown type {t!r}")


def _validate_inputs(
    decl: CapabilityDeclaration,
    raw_inputs: dict[str, Any],
    *,
    where: str,                                 # "prepare" or "commit"
    allowed_names: Optional[set[str]] = None,   # None=all; for commit, only editable names
) -> dict[str, Any]:
    """Strict whitelist + per-field type/enum coercion. Returns coerced dict.

    Required-ness is enforced at PREPARE only — at commit, edited_fields is
    sparse by design (the human only sends the fields they actually changed)."""
    specs_by_name = {s.name: s for s in decl.inputs}
    allowed = allowed_names if allowed_names is not None else set(specs_by_name)

    # Reject unknown / disallowed keys first. (At commit this is how the
    # "trying to edit a locked field" rejection happens — locked names are
    # not in `allowed_names`.)
    for name in raw_inputs:
        if name not in allowed:
            raise WriteSurfaceError(
                ErrorCode.INVALID_INPUT,
                f"field {name!r} is not accepted at {where} for {decl.name!r}",
                details={"field": name, "where": where},
            )

    out: dict[str, Any] = {}
    for name in allowed:
        spec = specs_by_name[name]
        if name not in raw_inputs:
            if spec.required and where == "prepare":
                raise WriteSurfaceError(
                    ErrorCode.INVALID_INPUT,
                    f"required field {name!r} missing",
                    details={"field": name},
                )
            continue
        try:
            out[name] = _coerce(spec, raw_inputs[name])
        except ValueError as exc:
            raise WriteSurfaceError(
                ErrorCode.INVALID_INPUT,
                f"field {name!r}: {exc}",
                details={"field": name, "type": spec.type},
            )
    return out


# ---------------------------------------------------------------------------
# JSON-safe serialization for JSONB storage
# ---------------------------------------------------------------------------

def _jsonable(value: Any) -> Any:
    """Coerce a Python value to a JSON-safe primitive for JSONB storage.
    UUIDs/Decimals/dates round-trip through strings; _coerce restores them at commit."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):     # datetime BEFORE date (datetime is a subclass of date)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return str(value)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def prepare(
    session: Session,
    current_user: User,
    *,
    capability_name: str,
    raw_inputs: dict[str, Any],
) -> PreparedActionHandle:
    """Validate inputs, build the preview, persist a pending prepared-action,
    return the opaque token + preview + editable field names."""
    # Late import keeps capabilities/registry decoupled at module-load time.
    from app.write_surface.registry import CAPABILITY_REGISTRY

    decl = CAPABILITY_REGISTRY.get(capability_name)
    if decl is None:
        raise WriteSurfaceError(
            ErrorCode.UNKNOWN_CAPABILITY,
            f"unknown capability {capability_name!r}",
            details={"capability": capability_name},
        )

    validated = _validate_inputs(decl, raw_inputs, where="prepare")

    try:
        preview = decl.validate(session, current_user, validated)
    except ValueError as exc:
        raise WriteSurfaceError(
            ErrorCode.INVALID_INPUT,
            str(exc),
            details={"capability": capability_name, "phase": "validate"},
        )

    specs_by_name = {s.name: s for s in decl.inputs}
    locked_data: dict[str, Any] = {}
    editable_data: dict[str, Any] = {}
    for name, value in validated.items():
        bucket = locked_data if specs_by_name[name].locked else editable_data
        bucket[name] = _jsonable(value)

    action_id = create_prepared_action(
        session,
        business_id=current_user.business_id,
        capability=decl.name,
        locked_data=locked_data,
        editable_data=editable_data,
        preview=preview,
    )
    return PreparedActionHandle(
        id=action_id,
        preview=preview,
        editable_fields=tuple(s.name for s in decl.inputs if not s.locked),
    )


def commit(
    session: Session,
    current_user: User,
    *,
    id: str,
    edited_fields: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Atomically claim the prepared action and execute. Returns the result dict
    from the capability's execute() hook."""
    edited_fields = edited_fields or {}

    # ENFORCEMENT (1): the store's atomic claim. The four subclasses collapse
    # to one opaque code; the specific subclass goes to the log, not the caller.
    try:
        row: PreparedAction = consume_prepared_action(
            session, id=id, business_id=current_user.business_id,
        )
    except PreparedActionError as exc:
        logger.warning(
            "prepared-action consume rejected: %s (%s)", type(exc).__name__, exc,
        )
        raise WriteSurfaceError(
            ErrorCode.PREPARED_ACTION_UNAVAILABLE,
            "prepared action is not available for commit",
        )

    from app.write_surface.registry import CAPABILITY_REGISTRY
    decl = CAPABILITY_REGISTRY.get(row.capability)
    if decl is None:
        # Config mismatch — the action exists but its capability is no longer
        # registered. Action is already consumed; the user has nothing to retry.
        raise WriteSurfaceError(
            ErrorCode.UNKNOWN_CAPABILITY,
            f"capability {row.capability!r} is not registered",
            details={"capability": row.capability},
        )

    # ENFORCEMENT (2): edited_fields keys must be a SUBSET of editable input
    # names. Anything else — a locked field, or a typo — is INVALID_INPUT.
    editable_names = {s.name for s in decl.inputs if not s.locked}
    validated_edits = _validate_inputs(
        decl, edited_fields, where="commit", allowed_names=editable_names,
    )

    # Reconstruct typed final inputs: locked + (editable defaults overridden by
    # validated edits). Locked/editable were stored JSON-able; re-coerce here.
    specs_by_name = {s.name: s for s in decl.inputs}
    final: dict[str, Any] = {}
    for name, jval in row.locked_data.items():
        final[name] = _coerce(specs_by_name[name], jval)
    for name, jval in row.editable_data.items():
        final[name] = _coerce(specs_by_name[name], jval) if jval is not None else None
    final.update(validated_edits)

    try:
        decl.recheck(session, current_user, final)
    except ValueError as exc:
        raise WriteSurfaceError(
            ErrorCode.INVALID_INPUT, str(exc), details={"phase": "recheck"},
        )

    # ENFORCEMENT (3): execute() failure -> clean error, NOT 500. The store row
    # stays consumed (atomic claim was durable). User must prepare again.
    try:
        return decl.execute(session, current_user, final)
    except Exception as exc:  # noqa: BLE001 — intentional per spec
        logger.exception("execute failed for capability %s", decl.name)
        raise WriteSurfaceError(
            ErrorCode.EXECUTE_FAILED,
            "could not complete — please prepare again",
            details={"capability": decl.name, "underlying": type(exc).__name__},
        )
