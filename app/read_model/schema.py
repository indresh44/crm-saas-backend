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

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Union


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
#: `in` was previously restricted to ENUM. Forcing it elsewhere meant the
#: agent had to do N sequential reads for a natural "fetch X and Y by name"
#: query — each extra value bought one extra LLM turn (~7k tokens + a round
#: trip). Multi-value equality is the same SQL primitive (column.in_(list))
#: for every type SQLAlchemy supports, so the restriction was arbitrary.
#: Extending it is a one-line correctness/perf win.
OPERATORS_BY_TYPE: dict[FieldType, tuple[Operator, ...]] = {
    FieldType.STRING: (Operator.EQ, Operator.CONTAINS, Operator.IN),
    FieldType.INTEGER: (Operator.EQ, Operator.LT, Operator.LTE, Operator.GT, Operator.GTE, Operator.BETWEEN, Operator.IN),
    FieldType.DECIMAL: (Operator.EQ, Operator.LT, Operator.LTE, Operator.GT, Operator.GTE, Operator.BETWEEN, Operator.IN),
    FieldType.DATE: (Operator.EQ, Operator.LT, Operator.LTE, Operator.GT, Operator.GTE, Operator.BETWEEN, Operator.IN),
    FieldType.DATETIME: (Operator.EQ, Operator.LT, Operator.LTE, Operator.GT, Operator.GTE, Operator.BETWEEN, Operator.IN),
    FieldType.BOOLEAN: (Operator.EQ, Operator.IN),
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


# ---------------------------------------------------------------------------
# Tenant scoping — declared per entity, enforced by the compiler.
# ---------------------------------------------------------------------------
# Two shapes only. ONE hop. The compiler asserts at startup that every
# ViaParent's parent_table resolves to a Direct-tenanted model (parent has a
# real business_id column). A ViaParent pointing at another ViaParent
# (hollow scoping) MUST fail loudly at import, never silently.
#
# WHY two dataclasses and not an enum string: each variant carries different
# data. Direct carries nothing (the predicate is "<entity>.business_id =
# :tenant"); ViaParent carries the parent table + join key + parent tenant
# column. Pattern-matching on isinstance keeps the compiler branches honest.

@dataclass(frozen=True)
class Direct:
    """Tenant scoping by a direct ``business_id`` column on the entity itself.
    This is the default and matches every entity declared today; no behavior
    changes for Direct entities."""


@dataclass(frozen=True)
class ViaParent:
    """Tenant scoping through ONE parent entity.

    The compiler emits an EXISTS subquery, never a JOIN:
        WHERE EXISTS (SELECT 1 FROM <parent_table>
                      WHERE <parent_table>.id = <entity>.<local_key>
                        AND <parent_table>.<parent_tenant_column> = :tenant)

    EXISTS is mandatory because it filters the outer row set without altering
    it — a JOIN could multiply rows, shadow columns, or interfere with
    aggregations. The compiler asserts at module load that ``parent_table``
    is a Direct-tenanted parent registered in the binding layer.
    """
    parent_table: str               # __tablename__ of the parent model
    local_key: str                  # FK column on THIS entity (e.g. "pipeline_id")
    parent_tenant_column: str       # tenant column on the parent (today: "business_id")


TenantScope = Union[Direct, ViaParent]


@dataclass(frozen=True)
class EntityDef:
    name: str                 # query-facing entity name
    table: str                # backing DB table (__tablename__)
    model: str                # backing SQLModel class
    fields: tuple[FieldDef, ...]          # raw columns
    virtual_fields: tuple[FieldDef, ...]  # derived/computed (canonical-governed)
    joins: tuple[JoinDef, ...]
    # Default preserves today's exact behavior for every existing EntityDef
    # literal (leads / invoices / customers). Only entities with no direct
    # business_id column should set ViaParent.
    tenant_scope: TenantScope = field(default_factory=Direct)


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
# PIPELINE_STAGES
# ===========================================================================
# Backing model: app/models/pipeline.py -> class PipelineStage, table
# "pipeline_stages".
#
# Tenant scoping: ViaParent("pipelines", "pipeline_id", "business_id").
# PipelineStage has NO direct business_id column — its tenant ownership is
# transitive via pipeline_stages.pipeline_id -> pipelines.business_id. The
# compiler emits an EXISTS subquery against `pipelines` for every read; see
# compiler.py:_apply_tenant_predicate.
#
# Excluded on purpose: nothing — there are no internal or sensitive columns
# on PipelineStage worth hiding from a tenant-scoped read.

_PIPELINE_STAGES = EntityDef(
    name="pipeline_stages",
    table="pipeline_stages",
    model="PipelineStage",
    fields=(
        FieldDef("id", FieldType.STRING, description="PipelineStage UUID (primary key)."),
        FieldDef("pipeline_id", FieldType.STRING, description="FK -> pipelines.id."),
        FieldDef("name", FieldType.STRING),
        FieldDef("position", FieldType.INTEGER, description="Display order within the pipeline."),
        FieldDef("color", FieldType.STRING, description="Hex / named UI color."),
    ),
    virtual_fields=(),
    joins=(),
    tenant_scope=ViaParent(
        parent_table="pipelines",
        local_key="pipeline_id",
        parent_tenant_column="business_id",
    ),
)


# ===========================================================================
# PAYMENTS  (Batch 1 — Direct)
# ===========================================================================
# Backing model: app/models/payment.py -> class Payment, table "payments".
#
# Excluded on purpose:
#   - business_id      (tenant key — injected by compiler, never queryable)
#   - voided_at, voided_reason, voided_by  (audit machinery; the LLM-visible
#                                            void state is the is_voided virtual)
#   - replaces_payment_id, edited_at       (audit lineage; not user-query targets)

_PAYMENTS = EntityDef(
    name="payments",
    table="payments",
    model="Payment",
    fields=(
        FieldDef("id", FieldType.STRING, description="Payment UUID (primary key)."),
        FieldDef("invoice_id", FieldType.STRING, description="FK -> invoices.id."),
        FieldDef("amount", FieldType.DECIMAL),
        FieldDef(
            "payment_method", FieldType.ENUM,
            enum_values=("upi", "cash", "bank_transfer", "card"),
            description="PaymentMethod enum (app/models/enums.py).",
        ),
        FieldDef("payment_date", FieldType.DATE),
        FieldDef("reference", FieldType.STRING),
        FieldDef("created_at", FieldType.DATETIME),
    ),
    virtual_fields=(
        FieldDef(
            "is_voided", FieldType.BOOLEAN, is_virtual=True,
            canonical_ref="batch-1: payments.voided_at IS NOT NULL",
            description="True if the payment row has been soft-voided. "
                        "Filter by is_voided=false to see active payments only.",
        ),
    ),
    joins=(
        JoinDef(
            name="invoice",
            target_table="invoices",
            local_key="invoice_id",
            target_key="id",
            exposes=(
                FieldDef("invoice_number", FieldType.STRING,
                         description="invoices.invoice_number"),
                FieldDef(
                    "invoice_status", FieldType.ENUM,
                    enum_values=("draft", "sent", "approved", "partial", "paid", "cancelled"),
                    description="invoices.status (renamed in projection to avoid clashing with the entity's own fields).",
                ),
            ),
            description="The invoice this payment was recorded against.",
        ),
    ),
)


# ===========================================================================
# CATALOG_ITEMS  (Batch 1 — Direct)
# ===========================================================================
# Backing model: app/models/catalog_item.py -> class CatalogItem, table
# "catalog_items".
#
# Excluded on purpose:
#   - business_id    (tenant key)
#   - deliverables   (JSONB list — opaque to the LLM; expose via a count
#                     virtual later if a use case appears)
#   - updated_at     (not a meaningful query target)

_CATALOG_ITEMS = EntityDef(
    name="catalog_items",
    table="catalog_items",
    model="CatalogItem",
    fields=(
        FieldDef("id", FieldType.STRING, description="CatalogItem UUID (primary key)."),
        FieldDef("name", FieldType.STRING),
        FieldDef("description", FieldType.STRING),
        FieldDef(
            "unit", FieldType.ENUM,
            enum_values=("piece", "sq_ft", "meter", "kg", "hour",
                         "session", "month", "trip", "lot", "custom"),
            description="CatalogItemUnit enum (app/models/enums.py).",
        ),
        FieldDef("custom_unit", FieldType.STRING,
                 description="Free-text unit when unit='custom'."),
        FieldDef("default_rate", FieldType.DECIMAL),
        FieldDef("gst_percent", FieldType.DECIMAL),
        FieldDef("sac_code", FieldType.STRING),
        FieldDef("is_active", FieldType.BOOLEAN),
        FieldDef("created_at", FieldType.DATETIME),
    ),
    virtual_fields=(),
    joins=(),
)


# ===========================================================================
# USERS  (Batch 1 — Direct)
# ===========================================================================
# Backing model: app/models/user.py -> class User, table "users".
#
# Excluded on purpose:
#   - business_id     (tenant key)
#   - phone           (privacy; not useful for assistant tasks)
#   - last_login_at   (privacy / audit; not a query target)
#   - created_at      (not requested for v1; can be added if needed)

_USERS = EntityDef(
    name="users",
    table="users",
    model="User",
    fields=(
        FieldDef("id", FieldType.STRING, description="User UUID (primary key)."),
        FieldDef("name", FieldType.STRING),
        FieldDef("email", FieldType.STRING),
        FieldDef(
            "role", FieldType.ENUM,
            enum_values=("owner", "manager", "staff"),
            description="UserRole enum (app/models/enums.py).",
        ),
        FieldDef("is_active", FieldType.BOOLEAN),
    ),
    virtual_fields=(),
    joins=(),
)


# ===========================================================================
# LEAD_FOLLOWUPS  (Batch 1 — ViaParent("leads", ...))
# ===========================================================================
# Backing model: app/models/lead_followup.py -> class LeadFollowup, table
# "lead_followups".
#
# Tenant scoping: ViaParent("leads", "lead_id", "business_id"). LeadFollowup
# has no direct business_id column; the EXISTS subquery enforces isolation
# via leads.business_id.

_LEAD_FOLLOWUPS = EntityDef(
    name="lead_followups",
    table="lead_followups",
    model="LeadFollowup",
    fields=(
        FieldDef("id", FieldType.STRING, description="LeadFollowup UUID."),
        FieldDef("lead_id", FieldType.STRING, description="FK -> leads.id."),
        FieldDef("scheduled_at", FieldType.DATETIME),
        FieldDef("note", FieldType.STRING),
        FieldDef(
            "status", FieldType.ENUM,
            enum_values=("pending", "done", "cancelled"),
            description="Stored as plain string in the DB; enumerated here.",
        ),
        FieldDef("created_by", FieldType.STRING, description="FK -> users.id."),
        FieldDef("completed_at", FieldType.DATETIME,
                 description="Set when the follow-up is marked done."),
    ),
    virtual_fields=(
        FieldDef(
            "is_overdue", FieldType.BOOLEAN, is_virtual=True,
            canonical_ref="batch-1: status='pending' AND scheduled_at < now",
            description="Per-row overdue flag. Distinct from leads.has_overdue_followup "
                        "(which is the EXISTS aggregate over a lead).",
        ),
        FieldDef(
            "is_completed", FieldType.BOOLEAN, is_virtual=True,
            canonical_ref="batch-1: status='done'",
            description="True iff status='done'.",
        ),
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
            description="The lead this follow-up is scheduled on.",
        ),
        JoinDef(
            name="customer",
            target_table="customers",
            local_key="lead_id",
            target_key="id",
            through="lead_followups.lead_id -> leads.customer_id -> customers.id",
            exposes=(
                FieldDef("customer_name", FieldType.STRING,
                         description="customers.name (via lead)"),
                FieldDef("customer_phone", FieldType.STRING,
                         description="customers.phone (via lead)"),
            ),
            description=(
                "The customer this follow-up's lead belongs to (two-hop). "
                "A follow-up whose lead has no customer_id will return null "
                "customer fields — expected, not an error. Mirrors the same "
                "two-hop pattern used by invoices.customer."
            ),
        ),
    ),
    tenant_scope=ViaParent(
        parent_table="leads",
        local_key="lead_id",
        parent_tenant_column="business_id",
    ),
)


# ===========================================================================
# LEAD_ACTIVITIES  (Batch 1 — ViaParent("leads", ...))
# ===========================================================================
# Backing model: app/models/lead.py -> class LeadActivity, table
# "lead_activities".
#
# The `type` enum is large because most activity rows are auto-logged by the
# write surface (stage changes, payments, invoices). Manually-logged subset
# is {call, whatsapp, meeting, note}; the rest are system events the LLM
# sees but does not create (the create-activity capability will lock `type`
# to the manual subset when added in a later batch).

_LEAD_ACTIVITIES = EntityDef(
    name="lead_activities",
    table="lead_activities",
    model="LeadActivity",
    fields=(
        FieldDef("id", FieldType.STRING, description="LeadActivity UUID."),
        FieldDef("lead_id", FieldType.STRING, description="FK -> leads.id."),
        FieldDef(
            "type", FieldType.ENUM,
            enum_values=(
                "call", "whatsapp", "meeting", "note",
                "status_change",
                "followup_scheduled", "followup_rescheduled",
                "followup_completed", "followup_cancelled",
                "invoice_created", "invoice_approved",
                "payment_recorded", "payment_edited",
                "payment_voided", "payment_moved",
            ),
            description="LeadActivityType enum (app/models/enums.py).",
        ),
        FieldDef("description", FieldType.STRING),
        FieldDef("created_by", FieldType.STRING, description="FK -> users.id."),
        FieldDef("created_at", FieldType.DATETIME),
    ),
    virtual_fields=(),
    joins=(
        JoinDef(
            name="lead",
            target_table="leads",
            local_key="lead_id",
            target_key="id",
            exposes=(
                FieldDef("lead_title", FieldType.STRING, description="leads.title"),
            ),
            description="The lead this activity belongs to.",
        ),
        JoinDef(
            name="customer",
            target_table="customers",
            local_key="lead_id",
            target_key="id",
            through="lead_activities.lead_id -> leads.customer_id -> customers.id",
            exposes=(
                FieldDef("customer_name", FieldType.STRING,
                         description="customers.name (via lead)"),
                FieldDef("customer_phone", FieldType.STRING,
                         description="customers.phone (via lead)"),
            ),
            description=(
                "The customer this activity's lead belongs to (two-hop). "
                "Same shape as lead_followups.customer and invoices.customer."
            ),
        ),
    ),
    tenant_scope=ViaParent(
        parent_table="leads",
        local_key="lead_id",
        parent_tenant_column="business_id",
    ),
)


# ===========================================================================
# INVOICE_ITEMS  (Batch 1 — ViaParent("invoices", ...))
# ===========================================================================
# Backing model: app/models/invoice_item.py -> class InvoiceItem, table
# "invoice_items".
#
# Excluded on purpose:
#   - deliverables   (JSONB list — opaque to the LLM)
#
# Note: `unit` is a plain string on this model (not the CatalogItemUnit
# enum), so it is typed STRING here, not ENUM.

_INVOICE_ITEMS = EntityDef(
    name="invoice_items",
    table="invoice_items",
    model="InvoiceItem",
    fields=(
        FieldDef("id", FieldType.STRING, description="InvoiceItem UUID."),
        FieldDef("invoice_id", FieldType.STRING, description="FK -> invoices.id."),
        FieldDef("name", FieldType.STRING),
        FieldDef("description", FieldType.STRING),
        FieldDef("unit", FieldType.STRING,
                 description="Free-text unit (defaults to 'piece'). Not the catalog enum."),
        FieldDef("quantity", FieldType.DECIMAL),
        FieldDef("unit_price", FieldType.DECIMAL),
        FieldDef(
            "gst_percent", FieldType.DECIMAL,
            description="DB CHECK restricts to {0, 5, 12, 18, 28}.",
        ),
        FieldDef("amount", FieldType.DECIMAL,
                 description="quantity * unit_price (pre-tax line amount)."),
        FieldDef("sac_code", FieldType.STRING),
        FieldDef("catalog_item_id", FieldType.STRING,
                 description="FK -> catalog_items.id (nullable; ad-hoc items have none)."),
    ),
    virtual_fields=(),
    joins=(),
    tenant_scope=ViaParent(
        parent_table="invoices",
        local_key="invoice_id",
        parent_tenant_column="business_id",
    ),
)


# ===========================================================================
# INVOICE_ADJUSTMENTS  (Batch 1 — ViaParent("invoices", ...))
# ===========================================================================
# Backing model: app/models/invoice_adjustment.py -> class InvoiceAdjustment,
# table "invoice_adjustments".
#
# `adjustment_type` is stored as a plain string on the model with a CHECK
# constraint pinning it to {'discount', 'write_off'}. We expose it as ENUM
# with those exact values.

_INVOICE_ADJUSTMENTS = EntityDef(
    name="invoice_adjustments",
    table="invoice_adjustments",
    model="InvoiceAdjustment",
    fields=(
        FieldDef("id", FieldType.STRING, description="InvoiceAdjustment UUID."),
        FieldDef("invoice_id", FieldType.STRING, description="FK -> invoices.id."),
        FieldDef("amount", FieldType.DECIMAL,
                 description="DB CHECK ensures amount > 0."),
        FieldDef(
            "adjustment_type", FieldType.ENUM,
            enum_values=("discount", "write_off"),
            description="InvoiceAdjustmentType enum; DB-stored as plain string + CHECK.",
        ),
        FieldDef("reason", FieldType.STRING),
        FieldDef("created_by", FieldType.STRING, description="FK -> users.id."),
        FieldDef("created_at", FieldType.DATETIME),
    ),
    virtual_fields=(),
    joins=(
        JoinDef(
            name="invoice",
            target_table="invoices",
            local_key="invoice_id",
            target_key="id",
            exposes=(
                FieldDef("invoice_number", FieldType.STRING,
                         description="invoices.invoice_number"),
            ),
            description="The invoice this adjustment was applied to.",
        ),
    ),
    tenant_scope=ViaParent(
        parent_table="invoices",
        local_key="invoice_id",
        parent_tenant_column="business_id",
    ),
)


# ===========================================================================
# The schema registry
# ===========================================================================

#: The complete read-model whitelist, keyed by query-facing entity name.
SCHEMA: dict[str, EntityDef] = {
    _LEADS.name: _LEADS,
    _INVOICES.name: _INVOICES,
    _CUSTOMERS.name: _CUSTOMERS,
    _PIPELINE_STAGES.name: _PIPELINE_STAGES,
    # Batch 1 — Direct
    _PAYMENTS.name: _PAYMENTS,
    _CATALOG_ITEMS.name: _CATALOG_ITEMS,
    _USERS.name: _USERS,
    # Batch 1 — ViaParent
    _LEAD_FOLLOWUPS.name: _LEAD_FOLLOWUPS,
    _LEAD_ACTIVITIES.name: _LEAD_ACTIVITIES,
    _INVOICE_ITEMS.name: _INVOICE_ITEMS,
    _INVOICE_ADJUSTMENTS.name: _INVOICE_ADJUSTMENTS,
}
