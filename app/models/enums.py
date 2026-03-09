from enum import Enum


class UserRole(str, Enum):
    OWNER = "owner"
    MANAGER = "manager"
    STAFF = "staff"


class LeadActivityType(str, Enum):
    CALL = "call"
    WHATSAPP = "whatsapp"
    MEETING = "meeting"
    NOTE = "note"
    STATUS_CHANGE = "status_change"


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


class InvoiceStatus(str, Enum):
    DRAFT = "draft"
    SENT = "sent"
    PAID = "paid"
    PARTIAL = "partial"
    OVERDUE = "overdue"


class PaymentMethod(str, Enum):
    UPI = "upi"
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    CARD = "card"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class AttachmentEntityType(str, Enum):
    LEAD = "lead"
    QUOTE = "quote"
    INVOICE = "invoice"
    TASK = "task"


class MessageDirection(str, Enum):
    INCOMING = "incoming"
    OUTGOING = "outgoing"


class MessageChannel(str, Enum):
    WHATSAPP = "whatsapp"
    SMS = "sms"
    EMAIL = "email"
