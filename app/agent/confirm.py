"""Post-confirmation commit — wraps engine.commit so the CLI doesn't talk to
layer 2 directly. This is NOT part of the loop; it's what runs after the human
has reviewed a prepared action and clicked confirm."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlmodel import Session

from app.models.user import User
from app.write_surface.engine import WriteSurfaceError, commit as ws_commit


@dataclass(frozen=True)
class ConfirmResult:
    ok: bool
    result: Optional[dict[str, Any]] = None   # from engine.commit on success
    error: Optional[dict] = None              # WriteSurfaceError.to_dict() on failure

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "result": self.result, "error": self.error}


def confirm_prepared_action(
    session: Session,
    current_user: User,
    action_id: str,
    edits: Optional[dict[str, Any]] = None,
) -> ConfirmResult:
    """Commit a prepared action. Returns a structured success/error envelope —
    never raises."""
    try:
        result = ws_commit(session, current_user, id=action_id, edited_fields=edits or {})
    except WriteSurfaceError as exc:
        return ConfirmResult(ok=False, error=exc.to_dict())
    return ConfirmResult(ok=True, result=result)
