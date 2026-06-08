"""Capability registry — the single place every write surface capability is wired.

Adding a future write = importing its DECLARATION here and adding one line.
Nothing else in the engine changes.
"""

from __future__ import annotations

from app.write_surface.capabilities import CapabilityDeclaration
from app.write_surface.caps.add_invoice_adjustment import DECLARATION as _ADD_INVOICE_ADJUSTMENT
from app.write_surface.caps.add_lead_note import DECLARATION as _ADD_LEAD_NOTE
from app.write_surface.caps.cancel_followup import DECLARATION as _CANCEL_FOLLOWUP
from app.write_surface.caps.cancel_invoice import DECLARATION as _CANCEL_INVOICE
from app.write_surface.caps.complete_followup import DECLARATION as _COMPLETE_FOLLOWUP
from app.write_surface.caps.create_catalog_item import DECLARATION as _CREATE_CATALOG_ITEM
from app.write_surface.caps.create_followup import DECLARATION as _CREATE_FOLLOWUP
from app.write_surface.caps.create_lead import DECLARATION as _CREATE_LEAD
from app.write_surface.caps.deactivate_catalog_item import DECLARATION as _DEACTIVATE_CATALOG_ITEM
from app.write_surface.caps.get_or_create_customer import DECLARATION as _GET_OR_CREATE_CUSTOMER
from app.write_surface.caps.record_payment import DECLARATION as _RECORD_PAYMENT
from app.write_surface.caps.reschedule_followup import DECLARATION as _RESCHEDULE_FOLLOWUP
from app.write_surface.caps.update_catalog_item import DECLARATION as _UPDATE_CATALOG_ITEM
from app.write_surface.caps.update_customer import DECLARATION as _UPDATE_CUSTOMER
from app.write_surface.caps.update_invoice import DECLARATION as _UPDATE_INVOICE
from app.write_surface.caps.update_lead import DECLARATION as _UPDATE_LEAD
from app.write_surface.caps.update_lead_stage import DECLARATION as _UPDATE_LEAD_STAGE
from app.write_surface.caps.update_payment_amount import DECLARATION as _UPDATE_PAYMENT_AMOUNT
from app.write_surface.caps.update_payment_metadata import DECLARATION as _UPDATE_PAYMENT_METADATA
from app.write_surface.caps.void_payment import DECLARATION as _VOID_PAYMENT


CAPABILITY_REGISTRY: dict[str, CapabilityDeclaration] = {
    _RECORD_PAYMENT.name: _RECORD_PAYMENT,
    _CREATE_LEAD.name: _CREATE_LEAD,
    _UPDATE_LEAD_STAGE.name: _UPDATE_LEAD_STAGE,
    _ADD_LEAD_NOTE.name: _ADD_LEAD_NOTE,
    _GET_OR_CREATE_CUSTOMER.name: _GET_OR_CREATE_CUSTOMER,
    # Follow-up capabilities (batch 3)
    _CREATE_FOLLOWUP.name: _CREATE_FOLLOWUP,
    _COMPLETE_FOLLOWUP.name: _COMPLETE_FOLLOWUP,
    _RESCHEDULE_FOLLOWUP.name: _RESCHEDULE_FOLLOWUP,
    _CANCEL_FOLLOWUP.name: _CANCEL_FOLLOWUP,
    # Selective-patch capabilities (batch 4)
    _UPDATE_LEAD.name: _UPDATE_LEAD,
    _UPDATE_CUSTOMER.name: _UPDATE_CUSTOMER,
    # Invoice operations (batch 5; create_invoice arrives separately)
    _CANCEL_INVOICE.name: _CANCEL_INVOICE,
    _ADD_INVOICE_ADJUSTMENT.name: _ADD_INVOICE_ADJUSTMENT,
    _UPDATE_INVOICE.name: _UPDATE_INVOICE,
    # Catalog operations (batch 6)
    _CREATE_CATALOG_ITEM.name: _CREATE_CATALOG_ITEM,
    _UPDATE_CATALOG_ITEM.name: _UPDATE_CATALOG_ITEM,
    _DEACTIVATE_CATALOG_ITEM.name: _DEACTIVATE_CATALOG_ITEM,
    # Payment corrections (batch 7 — the final capability batch)
    _VOID_PAYMENT.name: _VOID_PAYMENT,
    _UPDATE_PAYMENT_METADATA.name: _UPDATE_PAYMENT_METADATA,
    _UPDATE_PAYMENT_AMOUNT.name: _UPDATE_PAYMENT_AMOUNT,
}
