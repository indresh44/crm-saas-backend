"""Prepared-action store — the server-side safety mechanism for the write surface.

The contract this module enforces:
  - TTL: every prepared action expires 15 minutes after creation.
  - Single-use: a prepared action can be consumed AT MOST ONCE — guaranteed by
    a single conditional SQL UPDATE (compare-and-set), NOT a read-then-write.
  - Tenant: every consume verifies business_id IN THE SAME UPDATE statement, so
    a cross-tenant token can never even be touched, let alone flipped.
  - Audit retention: consumed/expired rows are KEPT (not deleted on use). The
    separate ``cleanup_old_prepared_actions`` function trims rows older than 1
    day (caller-scheduled — no background job here).

CRITICAL: see ``consume_prepared_action`` for the atomic compare-and-set. Do not
"simplify" it into a read-then-write — that introduces a race where two
concurrent consumes can both pass the precondition check before either commits.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, update
from sqlmodel import Session

from app.models.prepared_action import (
    STATUS_CONSUMED,
    STATUS_PENDING,
    PreparedAction,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: A prepared action is valid for commit for exactly this long after creation.
PREPARED_ACTION_TTL: timedelta = timedelta(minutes=15)

#: Default age cutoff for ``cleanup_old_prepared_actions`` (consumed and expired
#: rows are retained at least this long as an audit trail).
DEFAULT_RETENTION: timedelta = timedelta(days=1)


# ---------------------------------------------------------------------------
# Errors — each rejection is a distinct, machine-classifiable subclass so the
# (future) engine / API layer can map them to specific status codes without
# string-matching error messages.
# ---------------------------------------------------------------------------

class PreparedActionError(Exception):
    """Base class — catch this for any consume rejection."""


class PreparedActionNotFound(PreparedActionError):
    """No row exists with this id (token typo, wrong env, never created)."""


class PreparedActionWrongBusiness(PreparedActionError):
    """The row exists but belongs to a different tenant. Reported in this
    distinct shape so the engine can log it as a tenant-isolation event."""


class PreparedActionExpired(PreparedActionError):
    """The row exists, belongs to the caller, but ``now > expires_at``."""


class PreparedActionAlreadyConsumed(PreparedActionError):
    """The row exists, belongs to the caller, hasn't expired, but its status is
    already 'consumed' (a previous commit attempt succeeded)."""


# ---------------------------------------------------------------------------
# Store API
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """Single source for 'now' inside this module — always UTC, since TTL math
    is duration-based and timezone-agnostic."""
    return datetime.now(timezone.utc)


def _new_token() -> str:
    """Opaque, URL-safe, unguessable token. ~43 chars; fits the VARCHAR(64) PK."""
    return secrets.token_urlsafe(32)


def create_prepared_action(
    session: Session,
    *,
    business_id: UUID,
    capability: str,
    locked_data: dict[str, Any],
    editable_data: dict[str, Any],
    preview: str,
) -> str:
    """Insert a pending prepared-action row and return its opaque id.

    ``expires_at`` is set to ``created_at + PREPARED_ACTION_TTL``.
    The returned id is the ONLY handle the client receives. Commits.
    """
    now = _now()
    action = PreparedAction(
        id=_new_token(),
        business_id=business_id,
        capability=capability,
        locked_data=locked_data,
        editable_data=editable_data,
        preview=preview,
        status=STATUS_PENDING,
        created_at=now,
        expires_at=now + PREPARED_ACTION_TTL,
    )
    session.add(action)
    session.commit()
    return action.id


def consume_prepared_action(
    session: Session,
    *,
    id: str,
    business_id: UUID,
) -> PreparedAction:
    """Atomically claim a prepared action for commit; return the (now-consumed) row.

    The single-use guarantee is a SINGLE conditional UPDATE — compare-and-set:

        UPDATE prepared_actions
        SET    status = 'consumed'
        WHERE  id = :id
          AND  business_id = :business_id      -- tenant in WHERE: a cross-tenant
                                               -- token cannot be touched
          AND  status = 'pending'              -- single-use core
          AND  expires_at > :now               -- TTL: never flip an expired row

    Postgres serializes concurrent UPDATEs on the same row via a row lock; the
    later of two concurrent commits re-evaluates the WHERE against the new state
    and matches zero rows. ``result.rowcount == 1`` is the atomic guarantee.

    Diagnosis on ``rowcount == 0`` is POST-MORTEM only — it builds a specific
    error for the caller; it does NOT decide success. The check order below
    (business_id BEFORE status) avoids leaking the existence of a token to a
    different tenant via the error subclass.
    """
    now = _now()

    stmt = (
        update(PreparedAction)
        .where(
            PreparedAction.id == id,
            PreparedAction.business_id == business_id,
            PreparedAction.status == STATUS_PENDING,
            PreparedAction.expires_at > now,
        )
        .values(status=STATUS_CONSUMED)
    )
    result = session.execute(stmt)
    session.commit()

    if result.rowcount == 1:
        # Atomic claim succeeded. Re-load the row (status now == consumed).
        row = session.get(PreparedAction, id)
        # Defensive: shouldn't be None — we just successfully updated it.
        if row is None:
            raise PreparedActionError(f"prepared action {id!r} vanished after successful claim")
        return row

    # --- Diagnosis (post-mortem, does NOT affect correctness) ---
    row = session.get(PreparedAction, id)
    if row is None:
        raise PreparedActionNotFound(id)
    if row.business_id != business_id:
        # IMPORTANT: check tenant BEFORE status/expiry so we never reveal to a
        # different tenant whether the token exists or has been used.
        raise PreparedActionWrongBusiness(id)
    if row.status == STATUS_CONSUMED:
        raise PreparedActionAlreadyConsumed(id)
    if row.expires_at <= now:
        raise PreparedActionExpired(id)
    # Pending, ours, not expired — but the UPDATE matched 0 rows. Should be
    # unreachable; raise the base class so the caller still fails clearly.
    raise PreparedActionError(f"prepared action {id!r} unavailable for an unexpected reason")


def cleanup_old_prepared_actions(
    session: Session,
    *,
    older_than: timedelta = DEFAULT_RETENTION,
) -> int:
    """Delete rows whose ``created_at < now - older_than``.

    Targets both ``pending`` (expired-and-abandoned) and ``consumed`` rows once
    they are past the retention window. Returns the number deleted. NOT
    scheduled — the caller decides when to run this (a future cron / endpoint).
    """
    cutoff = _now() - older_than
    result = session.execute(
        delete(PreparedAction)
        .where(PreparedAction.created_at < cutoff)
        # Bulk delete: skip ORM session sync — we don't keep live ORM objects of
        # the deleted rows around, and avoiding sync sidesteps stale-attribute
        # access surprises on any object the caller happens to still reference.
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return result.rowcount
