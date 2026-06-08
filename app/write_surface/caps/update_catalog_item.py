"""update_catalog_item — selective patch of a catalog item's editable fields.

Locked   : item_id   (the LLM resolves; the human cannot retarget)
Editable : name, description, unit, custom_unit, default_rate, gst_percent,
           sac_code, is_active — all optional; absent fields are NOT touched

Selective patch is supported NATIVELY by the underlying service via
`CatalogItemUpdate(...).model_dump(exclude_unset=True)`. Same pattern as
update_lead / update_customer — splat only the user-supplied fields into
the DTO; the service leaves unsupplied columns alone.

Tenant scope: catalog_items is Direct-tenanted. The validate hook reads
through the read model — an out-of-tenant item returns ZERO rows and
becomes a clean 'not found' refusal.

Service guards (NOT re-implemented):
  * duplicate `name` (excluding self) -> 400 -> EXECUTE_FAILED at commit
  * unit=='custom' with empty custom_unit (using current values where
    unsupplied) -> 400 -> EXECUTE_FAILED

Preview: old -> new for each changed field.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.models.catalog_item import CatalogItemUpdate
from app.models.enums import CatalogItemUnit
from app.models.user import User
from app.read_model.compiler import Filter, ReadQuery, compile_query
from app.read_model.executor import execute_query
from app.services.catalog_item_service import (
    update_catalog_item as service_update_catalog_item,
)
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("item_id", "uuid", required=True, locked=True),
    InputSpec("name", "string", required=False, locked=False),
    InputSpec("description", "string", required=False, locked=False),
    InputSpec(
        "unit", "enum", required=False, locked=False,
        enum_values=tuple(u.value for u in CatalogItemUnit),
    ),
    InputSpec("custom_unit", "string", required=False, locked=False),
    InputSpec("default_rate", "decimal", required=False, locked=False),
    InputSpec("gst_percent", "decimal", required=False, locked=False),
    InputSpec("sac_code", "string", required=False, locked=False),
    InputSpec("is_active", "boolean", required=False, locked=False),
)

_EDITABLE_NAMES = tuple(s.name for s in _INPUTS if not s.locked)


def _load_item(session: Session, user: User, item_id: UUID) -> dict[str, Any]:
    cq = compile_query(
        ReadQuery(
            "catalog_items",
            filters=[Filter("id", "=", str(item_id))],
            select=[
                "id", "name", "description", "unit", "custom_unit",
                "default_rate", "gst_percent", "sac_code", "is_active",
            ],
        ),
        business_id=user.business_id,
    )
    rows = execute_query(cq, session).rows
    if not rows:
        raise ValueError(f"catalog item {item_id} not found for this business")
    return rows[0]


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "(blank)"
    return str(value)


def _validate(session: Session, user: User, inputs: dict[str, Any]) -> str:
    provided = {k: v for k, v in inputs.items() if k in _EDITABLE_NAMES}
    if not provided:
        raise ValueError(
            "no fields to update — provide at least one of: "
            + ", ".join(_EDITABLE_NAMES)
        )

    current = _load_item(session, user, inputs["item_id"])

    changes: list[str] = []
    for name, new_value in provided.items():
        old_value = current.get(name)
        if str(old_value) == str(new_value):
            continue
        changes.append(f"{name}: {_fmt(old_value)} → {_fmt(new_value)}")

    label = current.get("name") or "(unnamed)"
    if not changes:
        return f"Update catalog item {label!r}: (no changes — all provided values match current)."
    return f"Update catalog item {label!r} — {len(changes)} change(s):\n  " + "\n  ".join(changes)


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    patch = {k: v for k, v in inputs.items() if k in _EDITABLE_NAMES}
    if "unit" in patch and isinstance(patch["unit"], str):
        patch["unit"] = CatalogItemUnit(patch["unit"])
    item = service_update_catalog_item(
        session=session,
        current_user=user,
        item_id=inputs["item_id"],
        data=CatalogItemUpdate(**patch),
    )
    return {
        "item_id": str(item.id),
        "name": item.name,
        "is_active": item.is_active,
        "updated_fields": sorted(patch.keys()),
    }


DECLARATION = CapabilityDeclaration(
    name="update_catalog_item",
    description="Update a catalog item's editable fields. Selective patch — "
                "only fields you supply are changed. Service rejects "
                "duplicate names within the business and refuses "
                "unit='custom' without a custom_unit.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
