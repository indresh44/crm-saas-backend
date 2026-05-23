"""Read-model schema — the declarative query whitelist.

This module is the single source of truth the (future) query compiler validates
EVERY assistant-issued read request against. It is **not** a dump of the database
tables; it is a curated, deliberately narrower subset. If a field, operator, or
join is not declared here, the assistant cannot use it.

What this file is:
  - A static, declarative whitelist expressed as frozen dataclasses.

What this file is NOT (and must not become):
  - The request model, the query compiler, or any execution / SQL-building logic.
  - The virtual-field resolvers. Virtual fields below carry only a *reference* to
    the canonical definition that governs them (e.g. "§1" in
    ``Docs/canonical-definitions-v1.md``); the actual computation lands in the
    resolver layer later.

Design choice — why frozen dataclasses:
  - The schema is static configuration, not validated runtime input, so Pydantic's
    coercion/validation machinery buys little here. Plain dicts would lose type
    safety and IDE/refactor support. Frozen dataclasses give an immutable,
    typed, dependency-free declaration that reads like a spec. (When the request
    *model* is built later, that layer — which parses untrusted input — is the
    right place for Pydantic.)

Sources:
  - Field names confirmed against ``app/models/lead.py`` and ``app/models/invoice.py``.
  - Enum value sets confirmed against ``app/models/enums.py``.
  - Sensitive/internal exclusions per ``Docs/read-model-audit.md`` §1.
  - Virtual-field rules per ``Docs/canonical-definitions-v1.md``.

CRITICAL — tenant scoping:
  ``business_id`` is NEVER a queryable field and MUST NOT appear anywhere in this
  schema. Multi-tenant isolation is enforced by the compiler injecting
  ``business_id`` from the authenticated caller's context on every query. Allowing
  it as a filterable field would let a crafted request target another tenant.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Row limits
# ---------------------------------------------------------------------------

#: Absolute upper bound on rows any single read request may return. The compiler
#: clamps every requested limit to this value — a request asking for more is
#: capped, not rejected. This is a hard ceiling, not a default.
HARD_ROW_CAP: int = 50

#: Rows returned when a request does not specify a limit. Must be <= HARD_ROW_CAP.
DEFAULT_LIMIT: int = 20


# ---------------------------------------------------------------------------
# Field types and the central type -> operators map
# ---------------------------------------------------------------------------

class FieldType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    DATE = "date"
    DATETIME = "datetime"
    BOOLEAN = "boolean"
    ENUM = "enum"


class Operator(str, Enum):
    EQ = "="
    LT = "<"
    LTE = "<="
    GT = ">"
    GTE = ">="
    BETWEEN = "between"
    IN = "in"
    CONTAINS = "contains"


#: The ONE central operator map. Operators are derived from a field's type — there
#: are no per-field operator overrides, by design, so the rules stay uniform and
#: auditable. (UUID-ish identifier columns are typed STRING; the compiler may
#: choose to refuse CONTAINS on opaque-id fields, but that is a compiler policy,
#: not a schema concern.)
OPERATORS_BY_TYPE: dict[FieldType, tuple[Operator, ...]] = {
    FieldType.STRING: (Operator.EQ, Operator.CONTAINS),
    FieldType.INTEGER: (Operator.EQ, Operator.LT, Operator.LTE, Operator.GT, Operator.GTE, Operator.BETWEEN),
    FieldType.DECIMAL: (Operator.EQ, Operator.LT, Operator.LTE, Operator.GT, Operator.GTE, Operator.BETWEEN),
    FieldType.DATE: (Operator.EQ, Operator.LT, Operator.LTE, Operator.GT, Operator.GTE, Operator.BETWEEN),
    FieldType.DATETIME: (Operator.EQ, Operator.LT, Operator.LTE, Operator.GT, Operator.GTE, Operator.BETWEEN),
    FieldType.BOOLEAN: (Operator.EQ,),
    FieldType.ENUM: (Operator.EQ, Operator.IN),
}


# ---------------------------------------------------------------------------
# Declarative building blocks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldDef:
    """A single exposed field — filterable, sortable, and selectable.

    ``is_virtual`` distinguishes raw table columns from derived/computed fields.
    Virtual fields carry ``canonical_ref`` pointing at the governing section of
    ``Docs/canonical-definitions-v1.md``; their computation is resolved elsewhere.
    """

    name: str
    type: FieldType
    enum_values: Optional[tuple[str, ...]] = None
    is_virtual: bool = False
    canonical_ref: Optional[str] = None
    description: str = ""

    def __post_init__(self) -> None:
        # Declarative integrity guards (not query logic): keep the whitelist honest.
        if self.type is FieldType.ENUM and not self.enum_values:
            raise ValueError(f"enum field {self.name!r} must declare enum_values")
        if self.type is not FieldType.ENUM and self.enum_values:
            raise ValueError(f"non-enum field {self.name!r} must not declare enum_values")
        if self.is_virtual and not self.canonical_ref:
            raise ValueError(f"virtual field {self.name!r} must cite a canonical_ref")
        if self.name == "business_id":
            raise ValueError("business_id must never be a queryable field (injected by compiler)")

    @property
    def allowed_operators(self) -> tuple[Operator, ...]:
        return OPERATORS_BY_TYPE[self.type]


@dataclass(frozen=True)
class JoinDef:
    """A permitted join from an entity to a related entity.

    Joins not declared here are forbidden. ``exposes`` lists the joined columns
    that become selectable/filterable through this join (e.g. customer name).
    ``through`` documents a multi-hop path when the join is not a single FK.
    """

    name: str                 # logical join name, e.g. "customer"
    target_table: str         # backing table of the joined entity
    local_key: str            # column on THIS entity used to join
    target_key: str           # column on the TARGET table joined to
    through: Optional[str] = None       # multi-hop path description, if not a direct FK
    exposes: tuple[FieldDef, ...] = ()   # columns this join makes queryable
    description: str = ""


@dataclass(frozen=True)
class EntityDef:
    name: str                 # query-facing entity name
    table: str                # backing DB table (__tablename__)
    model: str                # backing SQLModel class
    fields: tuple[FieldDef, ...]          # raw columns
    virtual_fields: tuple[FieldDef, ...]  # derived/computed (canonical-governed)
    joins: tuple[JoinDef, ...]


# ===========================================================================
# LEADS
# ===========================================================================
# Backing model: app/models/lead.py  ->  class Lead, table "leads"
#
# Excluded on purpose:
#   - business_id      (tenant key — injected by compiler, never queryable)
#   - updated_at       (not a meaningful query target; created_at suffices)
#   - There is NO product / category field on leads — none exists, none planned.
# Note: `notes` was previously excluded as "internal", but the founder has decided
# it is ordinary business context the assistant may see — now exposed below.

_LEADS = EntityDef(
    name="leads",
    table="leads",
    model="Lead",
    fields=(
        FieldDef("id", FieldType.STRING, description="Lead UUID (primary key)."),
        FieldDef("title", FieldType.STRING),
        FieldDef(
            "source",
            FieldType.ENUM,
            enum_values=("walk_in", "whatsapp", "referral", "instagram", "justdial", "website", "other"),
            description="LeadSource enum (app/models/enums.py).",
        ),
        FieldDef("service_date", FieldType.DATE),
        FieldDef("follow_up_at", FieldType.DATETIME),
        FieldDef("estimated_value", FieldType.DECIMAL, description="Internal pipeline forecast; exposed for sort/filter."),
        FieldDef("created_at", FieldType.DATETIME),
        FieldDef("stage_id", FieldType.STRING, description="FK -> pipeline_stages.id."),
        FieldDef("customer_id", FieldType.STRING, description="FK -> customers.id (nullable)."),
        FieldDef("assigned_to", FieldType.STRING, description="FK -> users.id (nullable)."),
        FieldDef("notes", FieldType.STRING, description="Free-text business notes on the lead."),
    ),
    virtual_fields=(
        FieldDef("stage_name", FieldType.STRING, is_virtual=True,
                 canonical_ref="canonical-definitions-v1.md — stage_name / stage_color (§9)",
                 description="Resolved name of the lead's pipeline stage (JOIN to pipeline_stages)."),
        FieldDef("stage_color", FieldType.STRING, is_virtual=True,
                 canonical_ref="canonical-definitions-v1.md — stage_name / stage_color (§9)",
                 description="Resolved UI color of the lead's pipeline stage."),
        FieldDef("is_terminal", FieldType.BOOLEAN, is_virtual=True,
                 canonical_ref="canonical-definitions-v1.md — is_terminal / is_active (§10)",
                 description="Stage name in {won, lost, closed won, closed lost} (case-insensitive)."),
        FieldDef("is_active", FieldType.BOOLEAN, is_virtual=True,
                 canonical_ref="canonical-definitions-v1.md — is_terminal / is_active (§10)",
                 description="NOT is_terminal."),
        FieldDef("has_overdue_followup", FieldType.BOOLEAN, is_virtual=True,
                 canonical_ref="canonical-definitions-v1.md — has_overdue_followup (§11)",
                 description="Exists a pending follow-up with scheduled_at < now (business-local)."),
    ),
    joins=(
        JoinDef(
            name="customer",
            target_table="customers",
            local_key="customer_id",
            target_key="id",
            exposes=(
                FieldDef("customer_name", FieldType.STRING, description="customers.name"),
                FieldDef("customer_phone", FieldType.STRING, description="customers.phone"),
            ),
            description="The customer this lead belongs to (nullable).",
        ),
        JoinDef(
            name="pipeline_stage",
            target_table="pipeline_stages",
            local_key="stage_id",
            target_key="id",
            description=(
                "The pipeline stage this lead sits in. Drives the stage_name / "
                "stage_color virtual fields; no additional raw columns exposed."
            ),
        ),
    ),
)


# ===========================================================================
# INVOICES
# ===========================================================================
# Backing model: app/models/invoice.py  ->  class Invoice, table "invoices"
#
# Excluded on purpose:
#   - business_id                 (tenant key — injected by compiler, never queryable)
#   - pdf_url, pdf_generated_at   (storage internals, not query targets)
#   - cancelled_at, cancelled_reason (audit columns; cancellation surfaced via is_cancelled §4)
#   - booking_id, quote_id        (legacy/unused linkages — Quote/Booking are intentionally inactive)
#   - updated_at                  (not a meaningful query target)
#
# Field-name correction vs. the original request:
#   Invoices have NO `customer_id` column. The customer is reached only via the
#   lead (invoice.lead_id -> leads.customer_id -> customers.id). Customer name/phone
#   are therefore exposed through the (two-hop) `customer` join below, not as a raw
#   invoice column.

_INVOICES = EntityDef(
    name="invoices",
    table="invoices",
    model="Invoice",
    fields=(
        FieldDef("id", FieldType.STRING, description="Invoice UUID (primary key)."),
        FieldDef(
            "status",
            FieldType.ENUM,
            enum_values=("draft", "sent", "approved", "partial", "paid", "cancelled"),
            description="InvoiceStatus enum (app/models/enums.py). 'overdue' is NOT a stored status — see is_overdue.",
        ),
        FieldDef("issued_date", FieldType.DATE),
        FieldDef("due_date", FieldType.DATE),
        FieldDef("subtotal", FieldType.DECIMAL, description="Stored pre-tax sum (canonical §2)."),
        FieldDef("tax_total", FieldType.DECIMAL, description="Stored GST total (canonical §2)."),
        FieldDef("total_amount", FieldType.DECIMAL, description="Stored grand total (canonical §2)."),
        FieldDef("invoice_number", FieldType.STRING),
        FieldDef("created_at", FieldType.DATETIME),
        FieldDef("lead_id", FieldType.STRING, description="FK -> leads.id (nullable). Path to the customer."),
    ),
    virtual_fields=(
        FieldDef("balance", FieldType.DECIMAL, is_virtual=True,
                 canonical_ref="canonical-definitions-v1.md — balance (§1)",
                 description="total_amount - active payments - adjustments. Signed (may be negative)."),
        FieldDef("is_overdue", FieldType.BOOLEAN, is_virtual=True,
                 canonical_ref="canonical-definitions-v1.md — is_overdue (§5)",
                 description="due_date < today AND status not in {paid,cancelled,draft} AND balance > 0."),
        FieldDef("days_overdue", FieldType.INTEGER, is_virtual=True,
                 canonical_ref="canonical-definitions-v1.md — days_overdue (§6)",
                 description="(today - due_date).days; meaningful only when is_overdue."),
        FieldDef("document_type", FieldType.ENUM, enum_values=("estimate", "tax_invoice"),
                 is_virtual=True, canonical_ref="canonical-definitions-v1.md — document_type (§3)",
                 description="draft/sent -> estimate; approved/partial/paid/cancelled -> tax_invoice."),
        FieldDef("is_cancelled", FieldType.BOOLEAN, is_virtual=True,
                 canonical_ref="canonical-definitions-v1.md — is_cancelled (§4)",
                 description="status == cancelled."),
    ),
    joins=(
        JoinDef(
            name="lead",
            target_table="leads",
            local_key="lead_id",
            target_key="id",
            exposes=(
                FieldDef("lead_title", FieldType.STRING, description="leads.title"),
            ),
            description="The lead this invoice was raised against (nullable).",
        ),
        JoinDef(
            name="customer",
            target_table="customers",
            local_key="lead_id",
            target_key="id",
            through="invoice.lead_id -> leads.customer_id -> customers.id",
            exposes=(
                FieldDef("customer_name", FieldType.STRING, description="customers.name (via lead)"),
                FieldDef("customer_phone", FieldType.STRING, description="customers.phone (via lead)"),
            ),
            description=(
                "The customer, reached through the lead (two-hop; invoices have no direct customer_id). "
                "An invoice whose lead_id is null has no reachable customer, so customer_name / "
                "customer_phone will be null for such rows. This is expected behavior, not an error."
            ),
        ),
    ),
)


# ===========================================================================
# CUSTOMERS
# ===========================================================================
# Backing model: app/models/customer.py  ->  class Customer, table "customers"
#
# Exposed to support entity resolution ("find the customer named Rajesh",
# "look up customer by phone"). Founder confirmed `notes` is ordinary business
# context and is exposed.
#
# Excluded on purpose:
#   - business_id   (tenant key — injected by compiler, never queryable)
#   - updated_at    (mixin absent on this model anyway; not exposed)
#
# REQUIRED COMPILER WIRING (NOT done here — see report at end of v1-final task):
#   compiler.py needs `("customers"): Customer` added to `_ENTITY_MODELS`.
#   Without that one entry, compile_query(ReadQuery("customers", ...)) raises
#   KeyError. The compiler has an entity-binding registry; this is the
#   "entity hardcoding" the audit warned about.

_CUSTOMERS = EntityDef(
    name="customers",
    table="customers",
    model="Customer",
    fields=(
        FieldDef("id", FieldType.STRING, description="Customer UUID (primary key)."),
        FieldDef("name", FieldType.STRING),
        FieldDef("phone", FieldType.STRING, description="Display phone (as the user typed it)."),
        FieldDef(
            "phone_normalized", FieldType.STRING,
            description="Server-derived normalized phone — useful for exact lookups by digits.",
        ),
        FieldDef("email", FieldType.STRING),
        FieldDef("notes", FieldType.STRING, description="Free-text business notes on the customer."),
        FieldDef("address", FieldType.STRING),
        FieldDef("city", FieldType.STRING),
        FieldDef("state", FieldType.STRING),
        FieldDef("gst_number", FieldType.STRING),
        FieldDef("created_at", FieldType.DATETIME),
    ),
    virtual_fields=(),
    joins=(
        # Customer -> Lead is one-to-many (a customer has many leads), which the
        # current single-row JoinDef model can't cleanly express. The reverse
        # direction (leads.customer) is already declared on the leads entity.
    ),
)


# ===========================================================================
# PIPELINE_STAGES — NOT INCLUDED.
# ===========================================================================
# Schema declaration is straightforward, BUT the compiler's tenant predicate is
# hardcoded as `getattr(model, "business_id")` (compiler.py:_apply_tenant_predicate,
# line ~785). PipelineStage has NO `business_id` column — its tenant ownership
# is transitive via `pipeline_stages.pipeline_id -> pipelines.business_id`.
#
# Adding pipeline_stages as a top-level entity without first teaching the
# compiler to scope via an indirect path would EITHER:
#   - AttributeError at compile time (no `business_id` attribute on PipelineStage), OR
#   - (if naively fixed by skipping the predicate) leak every business's stages
#     to every other business — a tenant-isolation breach.
#
# The clean fix is a per-entity tenant_scope declaration in EntityDef, e.g.:
#   EntityDef(... tenant_scope=ViaJoin(
#       intermediate_model="pipelines",
#       local_key="pipeline_id", target_key="id",
#       intermediate_tenant_column="business_id"))
# read by the compiler when building the tenant WHERE. That's a compiler change
# (architectural, not mechanical) and is explicitly out of scope for this task.

# ===========================================================================
# The schema registry
# ===========================================================================

#: The complete read-model whitelist, keyed by query-facing entity name.
SCHEMA: dict[str, EntityDef] = {
    _LEADS.name: _LEADS,
    _INVOICES.name: _INVOICES,
    _CUSTOMERS.name: _CUSTOMERS,
}
