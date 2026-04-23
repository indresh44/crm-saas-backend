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
    INVOICE_APPROVED = "invoice_approved"
    PAYMENT_RECORDED = "payment_recorded"


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
