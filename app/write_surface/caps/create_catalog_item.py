"""create_catalog_item — add a new reusable line-item template.

This is a CREATE; there is no LOCKED identity input. All inputs are
EDITABLE. `name` and `default_rate` are required by the service — declared
as required here too so the engine refuses the prepare cleanly instead of
the LLM seeing a 500 at commit.

Service guards (NOT re-implemented):
  * duplicate `name` within the business -> rejected -> EXECUTE_FAILED at commit
  * unit=='custom' with empty custom_unit -> rejected
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlmodel import Session

from app.models.catalog_item import CatalogItemCreate
from app.models.enums import CatalogItemUnit
from app.models.user import User
from app.services.catalog_item_service import (
    create_catalog_item as service_create_catalog_item,
)
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("name", "string", required=True, locked=False),
    InputSpec("default_rate", "decimal", required=True, locked=False),
    InputSpec("description", "string", required=False, locked=False),
    InputSpec(
        "unit", "enum", required=False, locked=False,
        enum_values=tuple(u.value for u in CatalogItemUnit),
    ),
    InputSpec("custom_unit", "string", required=False, locked=False),
    InputSpec("gst_percent", "decimal", required=False, locked=False),
    InputSpec("sac_code", "string", required=False, locked=False),
)


def _fmt_money(amount: Decimal) -> str:
    return f"₹{amount:,.2f}"


def _validate(session: Session, user: User, inputs: dict[str, Any]) -> str:
    name: str = inputs["name"]
    rate: Decimal = inputs["default_rate"]
    # gst_percent is optional; the service defaults to 18% if omitted, but
    # show 'default 18%' in the preview rather than fabricating a number.
    gst = inputs.get("gst_percent")
    unit = inputs.get("unit") or "piece"
    custom_unit = inputs.get("custom_unit")
    unit_label = custom_unit if (unit == "custom" and custom_unit) else unit

    parts = [
        f"Create catalog item {name!r}",
        f"at {_fmt_money(rate)} per {unit_label}",
        f"({gst}% GST)" if gst is not None else "(default 18% GST)",
    ]
    return " ".join(parts) + "."


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    # Engine coerces enum input to its str value; CatalogItemCreate accepts
    # the enum or its string. Be explicit to keep the type contract readable.
    payload = dict(inputs)
    if "unit" in payload and isinstance(payload["unit"], str):
        payload["unit"] = CatalogItemUnit(payload["unit"])
    item = service_create_catalog_item(
        session=session,
        current_user=user,
        data=CatalogItemCreate(**payload),
    )
    return {
        "item_id": str(item.id),
        "name": item.name,
        "default_rate": str(item.default_rate),
        "gst_percent": str(item.gst_percent),
        "unit": item.unit.value if hasattr(item.unit, "value") else str(item.unit),
        "is_active": item.is_active,
    }


DECLARATION = CapabilityDeclaration(
    name="create_catalog_item",
    description="Create a new catalog item (reusable invoice line-item "
                "template). Service rejects duplicate names within the "
                "business and refuses unit='custom' without a custom_unit.",
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
