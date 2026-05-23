"""Capability registry — the single place every write surface capability is wired.

Adding a future write = importing its DECLARATION here and adding one line.
Nothing else in the engine changes.
"""

from __future__ import annotations

from app.write_surface.capabilities import CapabilityDeclaration
from app.write_surface.caps.add_lead_note import DECLARATION as _ADD_LEAD_NOTE
from app.write_surface.caps.create_lead import DECLARATION as _CREATE_LEAD
from app.write_surface.caps.get_or_create_customer import DECLARATION as _GET_OR_CREATE_CUSTOMER
from app.write_surface.caps.record_payment import DECLARATION as _RECORD_PAYMENT
from app.write_surface.caps.update_lead_stage import DECLARATION as _UPDATE_LEAD_STAGE


CAPABILITY_REGISTRY: dict[str, CapabilityDeclaration] = {
    _RECORD_PAYMENT.name: _RECORD_PAYMENT,
    _CREATE_LEAD.name: _CREATE_LEAD,
    _UPDATE_LEAD_STAGE.name: _UPDATE_LEAD_STAGE,
    _ADD_LEAD_NOTE.name: _ADD_LEAD_NOTE,
    _GET_OR_CREATE_CUSTOMER.name: _GET_OR_CREATE_CUSTOMER,
}
