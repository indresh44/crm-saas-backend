from enum import Enum


class UserRole(str, Enum):
    OWNER = "owner"
    MANAGER = "manager"
    STAFF = "staff"


class AuthProvider(str, Enum):
    """Authentication provider types."""

    EMAIL = "email"
    GOOGLE = "google"
    PHONE = "phone"


class LeadActivityType(str, Enum):
    CALL = "call"
    WHATSAPP = "whatsapp"
    MEETING = "meeting"
    NOTE = "note"
    STATUS_CHANGE = "status_change"
    FOLLOWUP_SCHEDULED = "followup_scheduled"
    FOLLOWUP_RESCHEDULED = "followup_rescheduled"
    FOLLOWUP_COMPLETED = "followup_completed"
    FOLLOWUP_CANCELLED = "followup_cancelled"
    INVOICE_CREATED = "invoice_created"
    INVOICE_SENT = "invoice_sent"              # new (DRAFT→SENT transition)
    INVOICE_APPROVED = "invoice_approved"
    INVOICE_CANCELLED = "invoice_cancelled"    # new
    INVOICE_ADJUSTED = "invoice_adjusted"      # new
    PAYMENT_RECORDED = "payment_recorded"
    PAYMENT_EDITED = "payment_edited"
    PAYMENT_VOIDED = "payment_voided"
    PAYMENT_MOVED = "payment_moved"
    LEAD_CREATED = "lead_created"              # new (lifecycle start)
    LEAD_UPDATED = "lead_updated"              # new (field edits)


class ActorType(str, Enum):
    """Who/what produced an activity row. Distinct from `created_by` (which is
    the user id, if any). The diary uses this to filter "what the AI did" vs
    "what the owner did" vs "what the system observed."
      * HUMAN  — owner via the UI / a direct HTTP route
      * AI     — write-surface capability invoked through the agent loop
      * TASK   — same as AI but spawned by the multi-task runner; carries task id
      * SYSTEM — webhook (incoming WhatsApp), auto-recompute, scheduled jobs"""
    HUMAN = "human"
    AI = "ai"
    TASK = "task"
    SYSTEM = "system"


class QuoteStatus(str, Enum):
    DRAFT = "draft"
    SENT = "sent"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class BookingStatus(str, Enum):
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class MeetingStatus(str, Enum):
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class InvoiceStatus(str, Enum):
    DRAFT = "draft"
    SENT = "sent"
    APPROVED = "approved"
    PARTIAL = "partial"
    PAID = "paid"
    CANCELLED = "cancelled"


class InvoiceAdjustmentType(str, Enum):
    DISCOUNT = "discount"
    WRITE_OFF = "write_off"


class LeadSource(str, Enum):
    WALK_IN = "walk_in"
    WHATSAPP = "whatsapp"
    REFERRAL = "referral"
    INSTAGRAM = "instagram"
    JUSTDIAL = "justdial"
    WEBSITE = "website"
    OTHER = "other"


class PaymentMethod(str, Enum):
    UPI = "upi"
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    CARD = "card"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class FollowupStatus(str, Enum):
    """Real Postgres enum — stored on lead_followups.status (DB type
    `followup_status`)."""

    PENDING = "pending"
    DONE = "done"
    CANCELLED = "cancelled"


class Outcome(str, Enum):
    """App-side only. Lives in lead_activities.payload JSON, NOT a DB enum —
    keep additions cheap. Covers the two channels we currently log outcomes
    for (call + WhatsApp). Keep in sync with the resolve-followup UI."""

    NO_ANSWER = "no_answer"
    BUSY = "busy"
    SPOKE_INTERESTED = "spoke_interested"
    SPOKE_LATER = "spoke_later"
    SPOKE_NOT_INTERESTED = "spoke_not_interested"
    WA_SENT = "wa_sent"
    WA_REPLIED = "wa_replied"
    WA_NOT_REPLIED = "wa_not_replied"
    WA_NOT_INTERESTED = "wa_not_interested"
    # --- Deprecated (no new writes). Kept so historical lead_activities.payload
    # values still parse via Outcome(...). Removed from all live buckets + UI. ---
    WRONG_NUMBER = "wrong_number"
    WA_LATER = "wa_later"
    WA_NO_NUMBER = "wa_no_number"


class ResultAction(str, Enum):
    """App-side only. The post-outcome disposition picked in the resolve
    flow — what the user chose to do with the follow-up after logging the
    outcome. Stored in lead_activities.payload."""

    RESCHEDULED = "rescheduled"
    NEXT_FOLLOWUP = "next_followup"
    CLOSED = "closed"
    MARKED_DONE = "marked_done"
    # Retry outcome logged WITHOUT touching the follow-up — it stays pending
    # on its date so the owner can come back to it. Tally still increments
    # (derived from the activity row).
    LOGGED = "logged"


class DemandTagOrigin(str, Enum):
    """Where a demand_tag came from. AI = produced by the per-enquiry
    intelligence service; OWNER = curated/created by the business owner
    via the UI (future)."""

    AI = "ai"
    OWNER = "owner"


class CatalogItemUnit(str, Enum):
    """Common units. The 'custom' value lets owners type their own unit."""

    PIECE = "piece"
    SQ_FT = "sq_ft"
    METER = "meter"
    KG = "kg"
    HOUR = "hour"
    SESSION = "session"
    MONTH = "month"
    TRIP = "trip"
    LOT = "lot"
    CUSTOM = "custom"


class AttachmentEntityType(str, Enum):
    LEAD = "lead"
    LEAD_ACTIVITY = "lead_activity"
    PAYMENT = "payment"
    CATALOG = "catalog"
    QUOTE = "quote"
    INVOICE = "invoice"
    INVOICE_ITEM = "invoice_item"
    TASK = "task"


class MessageDirection(str, Enum):
    INCOMING = "incoming"
    OUTGOING = "outgoing"


class MessageChannel(str, Enum):
    WHATSAPP = "whatsapp"
    SMS = "sms"
    EMAIL = "email"


class WhatsAppMessageDirection(str, Enum):
    INCOMING = "incoming"
    OUTGOING = "outgoing"


class WhatsAppMessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"
    INTERACTIVE = "interactive"
    TEMPLATE = "template"
    UNKNOWN = "unknown"


class WhatsAppMessageStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class WhatsAppMessageEventType(str, Enum):
    WEBHOOK_RECEIVED = "webhook_received"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
