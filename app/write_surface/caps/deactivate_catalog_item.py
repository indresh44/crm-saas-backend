"""deactivate_catalog_item — soft-delete (is_active = False) a catalog item.

Locked   : item_id (the LLM resolves; the human cannot retarget)
Editable : — (no editable fields; deactivation has no per-call options)

This is a soft state change, NOT a hard delete. The row stays in the DB
and remains visible via filters on is_active=false. The service simply
flips is_active to False.

Tenant scope: catalog_items is Direct-tenanted. Validate reads through
the read model — out-of-tenant item returns ZERO rows -> clean refusal.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.services.catalog_item_service import (
    deactivate_catalog_item as service_deactivate_catalog_item,
)
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("item_id", "uuid", required=True, locked=True),
)


def _load_item(session: Session, user: User, item_id: UUID) -> dict[str, Any]:
    cq = compile_query(
        ReadQuery(
            "catalog_items",
            filters=[Filter("id", "=", str(item_id))],
            select=["id", "name", "is_active"],
        ),
        business_id=user.business_id,
    )
    rows = execute_query(cq, session).rows
    if not rows:
        raise ValueError(f"catalog item {item_id} not found for this business")
    return rows[0]


def _validate(session: Session, user: User, inputs: dict[str, Any]) -> str:
    item = _load_item(session, user, inputs["item_id"])
    name = item.get("name") or "(unnamed)"
    if item.get("is_active") is False:
        return f"Deactivate catalog item {name!r}: already inactive (no-op)."
    return (
        f"Deactivate catalog item {name!r} (set is_active=false). "
        f"This is a soft change; the row stays in the catalog and can be "
        f"reactivated via update_catalog_item."
    )


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    item = service_deactivate_catalog_item(
        session=session,
        current_user=user,
        item_id=inputs["item_id"],
    )
    return {
        "item_id": str(item.id),
        "name": item.name,
        "is_active": item.is_active,
    }


DECLARATION = CapabilityDeclaration(
    name="deactivate_catalog_item",
    description="Soft-deactivate a catalog item (set is_active=false). The "
                "row stays in the DB; use update_catalog_item to reactivate.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
