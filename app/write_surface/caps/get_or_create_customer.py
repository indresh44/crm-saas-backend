"""get_or_create_customer capability — resolve-or-create a customer by phone.

Locked   : phone, name      (LLM proposes; the human cannot change identity at commit)
Editable : notes            (free-text the human may adjust)

Identity key is the normalized phone — the same key the rest of the app uses to
de-dupe customers (audit found `_confirm_create_lead` already does a
`get_customer_by_phone` check; we reuse the same service path here).

Branch is explicit to the human in the preview: "existing" vs "new". That is
the whole point of the confirmation — the human sees what's about to happen.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlmodel import Session

from app.models.customer import CustomerCreateRequest
from app.models.user import User
from app.services.customer_service import (
    create_customer as service_create_customer,
    get_customer_by_phone as service_get_customer_by_phone,
)
from app.write_surface.capabilities import CapabilityDeclaration, InputSpec


_INPUTS: tuple[InputSpec, ...] = (
    InputSpec("phone", "string", required=True, locked=True),
    InputSpec("name", "string", required=True, locked=True),
    InputSpec("notes", "string", required=False, locked=False),
)


def _lookup(session: Session, user: User, phone: str):
    """Wrapper around customer_service.get_customer_by_phone that maps the
    400-on-bad-phone HTTPException into a ValueError so the engine surfaces it
    as INVALID_INPUT instead of EXECUTE_FAILED at the validate stage."""
    try:
        return service_get_customer_by_phone(session=session, current_user=user, phone=phone)
    except HTTPException as exc:
        # The service raises 400 on a phone that doesn't normalize.
        raise ValueError(f"invalid phone: {exc.detail}")


def _validate(session: Session, user: User, inputs: dict[str, Any]) -> str:
    """Look up; the preview tells the human exactly which branch will run."""
    phone = inputs["phone"]
    name = inputs["name"]
    existing = _lookup(session, user, phone)
    if existing is not None:
        return f"Existing customer: {existing.name} ({existing.phone}) — will be used."
    return f"New customer will be created: {name} ({phone})."


def _execute(session: Session, user: User, inputs: dict[str, Any]) -> dict[str, Any]:
    """Re-resolve at commit time (someone else may have created the same phone
    between prepare and commit). Return the customer id either way, with
    `created` telling the caller which branch ran.

    NOTE on race: a tiny window exists between the re-resolve and create_customer.
    If a parallel call creates the same phone in that window, the service raises
    409 — engine wraps as EXECUTE_FAILED. Acceptable for an assistant flow.
    """
    phone = inputs["phone"]
    name = inputs["name"]
    notes = inputs.get("notes")

    existing = _lookup(session, user, phone)
    if existing is not None:
        return {"customer_id": str(existing.id), "created": False}

    customer = service_create_customer(
        session=session,
        current_user=user,
        data=CustomerCreateRequest(name=name, phone=phone, notes=notes),
    )
    return {"customer_id": str(customer.id), "created": True}


DECLARATION = CapabilityDeclaration(
    name="get_or_create_customer",
    description=(
        "Resolve a customer by phone within the business; create one if no match. "
        "Returns the customer id and a `created` boolean indicating which branch ran."
    ),
    inputs=_INPUTS,
    validate=_validate,
    execute=_execute,
)
