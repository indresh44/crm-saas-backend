"""
Admin business service — list, detail, hard-delete (cascade).

This service is only ever invoked from the admin router, which is itself
only registered when ENABLE_ADMIN_ROUTES is true.

The cascade delete walks every child table that references either the
business directly or one of its child entities. It runs in a single
transaction — either the business and all its data are gone, or nothing
changes. The order matters: children before parents, to avoid FK
violations.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
import uuid

from sqlalchemy import text
from sqlmodel import Session, select

from app.models.business import Business
from app.models.invoice import Invoice
from app.models.lead import Lead
from app.models.payment import Payment
from app.models.user import User
from app.repositories.admin_audit_repository import write_audit


# Order matters. Each row must come after every table that references it
# (otherwise Postgres throws FK violations under default ON DELETE NO ACTION).
# Notable constraints we honor below:
#   invoices.booking_id → bookings.id (optional)        ⇒ delete invoices before bookings
#   invoice_templates.source_invoice_id → invoices.id   ⇒ delete invoice_templates before invoices
#   bookings.{quote_id,lead_id} → quotes/leads (NOT NULL) ⇒ delete bookings before quotes/leads
#   quotes.lead_id → leads (NOT NULL)                   ⇒ delete quotes before leads
#   leads.stage_id → pipeline_stages (NOT NULL)         ⇒ delete leads before pipeline_stages
#   businesses.owner_user_id → users.id (cycle)         ⇒ null first, then delete users, then business
_CASCADE_STATEMENTS: list[str] = [
    # ── lead-scoped children (reference leads + users) ──
    "DELETE FROM lead_activities WHERE lead_id IN (SELECT id FROM leads WHERE business_id = :biz)",
    "DELETE FROM lead_followups  WHERE lead_id IN (SELECT id FROM leads WHERE business_id = :biz)",

    # ── invoice-scoped children (reference invoices + users) ──
    "DELETE FROM payments            WHERE business_id = :biz",
    "DELETE FROM invoice_adjustments WHERE invoice_id IN (SELECT id FROM invoices WHERE business_id = :biz)",
    "DELETE FROM invoice_items       WHERE invoice_id IN (SELECT id FROM invoices WHERE business_id = :biz)",

    # ── template children, then templates (templates reference invoices) ──
    "DELETE FROM invoice_template_items WHERE template_id IN (SELECT id FROM invoice_templates WHERE business_id = :biz)",
    "DELETE FROM invoice_templates      WHERE business_id = :biz",

    # ── quote children ──
    "DELETE FROM quote_items WHERE quote_id IN (SELECT id FROM quotes WHERE business_id = :biz)",

    # ── invoices come BEFORE bookings (invoice.booking_id) ──
    "DELETE FROM invoices WHERE business_id = :biz",

    # ── bookings come before quotes + leads ──
    "DELETE FROM bookings WHERE business_id = :biz",

    # ── quotes come before leads ──
    "DELETE FROM quotes WHERE business_id = :biz",

    # ── tasks/meetings/messages reference leads + customers + users ──
    "DELETE FROM tasks    WHERE business_id = :biz",
    "DELETE FROM meetings WHERE business_id = :biz",
    "DELETE FROM messages WHERE business_id = :biz",

    # ── chat ──
    "DELETE FROM chat_messages WHERE thread_id IN (SELECT id FROM chat_threads WHERE business_id = :biz)",
    "DELETE FROM chat_threads  WHERE business_id = :biz",

    # ── whatsapp (events → messages → conversations → accounts) ──
    "DELETE FROM whatsapp_message_events WHERE business_id = :biz",
    "DELETE FROM whatsapp_messages       WHERE business_id = :biz",
    "DELETE FROM whatsapp_conversations  WHERE business_id = :biz",
    "DELETE FROM whatsapp_accounts       WHERE business_id = :biz",

    # ── attachments (entity_id is unindexed soft reference, no FK) ──
    "DELETE FROM attachments WHERE business_id = :biz",

    # ── leads come before pipeline_stages (leads.stage_id NOT NULL) and customers ──
    "DELETE FROM leads     WHERE business_id = :biz",
    "DELETE FROM customers WHERE business_id = :biz",

    # ── pipelines: stages first, then pipelines ──
    "DELETE FROM pipeline_stages WHERE pipeline_id IN (SELECT id FROM pipelines WHERE business_id = :biz)",
    "DELETE FROM pipelines       WHERE business_id = :biz",

    # ── catalog_items (now safe; invoice_items/quote_items/template_items all gone) ──
    "DELETE FROM catalog_items WHERE business_id = :biz",

    # ── user-scoped tables ──
    "DELETE FROM notifications         WHERE user_id IN (SELECT id FROM users WHERE business_id = :biz)",
    "DELETE FROM refresh_tokens        WHERE user_id IN (SELECT id FROM users WHERE business_id = :biz)",
    "DELETE FROM password_reset_tokens WHERE user_id IN (SELECT id FROM users WHERE business_id = :biz)",
    "DELETE FROM auth_identities       WHERE user_id IN (SELECT id FROM users WHERE business_id = :biz)",

    # ── break the businesses ↔ users FK cycle, then delete users, then business ──
    "UPDATE businesses SET owner_user_id = NULL WHERE id = :biz",
    "DELETE FROM users WHERE business_id = :biz",
    "DELETE FROM businesses WHERE id = :biz",
]


# ───────────────────────────── list / detail ─────────────────────────────


def list_businesses(session: Session) -> list[dict[str, Any]]:
    """
    Return every business with owner email, signup date, last login, and
    high-level counts. One row per business.
    """
    sql = text(
        """
        SELECT
            b.id::text                              AS id,
            b.name                                  AS name,
            b.phone                                 AS phone,
            b.preferred_language                    AS preferred_language,
            b.timezone                              AS timezone,
            b.onboarding_status                     AS onboarding_status,
            b.created_at                            AS created_at,
            owner.email                             AS owner_email,
            owner.name                              AS owner_name,
            owner.last_login_at                     AS owner_last_login_at,
            (SELECT COUNT(*) FROM users   u WHERE u.business_id   = b.id) AS user_count,
            (SELECT COUNT(*) FROM leads   l WHERE l.business_id   = b.id) AS lead_count,
            (SELECT COUNT(*) FROM invoices i WHERE i.business_id  = b.id) AS invoice_count,
            (SELECT COALESCE(SUM(p.amount), 0)
                FROM payments p
                WHERE p.business_id = b.id AND p.voided_at IS NULL) AS payments_captured,
            (SELECT COALESCE(SUM(i.total_amount), 0)
                FROM invoices i
                WHERE i.business_id = b.id AND i.status NOT IN ('paid', 'cancelled')) AS outstanding_amount
        FROM businesses b
        LEFT JOIN users owner ON owner.id = b.owner_user_id
        ORDER BY b.created_at DESC
        """
    )
    rows = session.execute(sql).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "phone": r.phone,
            "preferred_language": r.preferred_language,
            "timezone": r.timezone,
            "onboarding_status": r.onboarding_status,
            "created_at": r.created_at,
            "owner_email": r.owner_email,
            "owner_name": r.owner_name,
            "owner_last_login_at": r.owner_last_login_at,
            "user_count": int(r.user_count or 0),
            "lead_count": int(r.lead_count or 0),
            "invoice_count": int(r.invoice_count or 0),
            "payments_captured": float(r.payments_captured or 0),
            "outstanding_amount": float(r.outstanding_amount or 0),
        }
        for r in rows
    ]


def get_business_detail(session: Session, business_id: uuid.UUID) -> Optional[dict[str, Any]]:
    business = session.get(Business, business_id)
    if business is None:
        return None

    users = list(session.exec(select(User).where(User.business_id == business_id)).all())
    counts = _count_business_data(session, business_id)

    recent_leads = session.exec(
        select(Lead).where(Lead.business_id == business_id).order_by(Lead.created_at.desc()).limit(10)
    ).all()
    recent_invoices = session.exec(
        select(Invoice).where(Invoice.business_id == business_id).order_by(Invoice.created_at.desc()).limit(10)
    ).all()
    recent_payments = session.exec(
        select(Payment).where(Payment.business_id == business_id).order_by(Payment.created_at.desc()).limit(10)
    ).all()

    owner = None
    if business.owner_user_id:
        owner = session.get(User, business.owner_user_id)

    return {
        "id": str(business.id),
        "name": business.name,
        "phone": business.phone,
        "email": business.email,
        "preferred_language": business.preferred_language,
        "timezone": business.timezone,
        "onboarding_status": business.onboarding_status,
        "onboarding_method": business.onboarding_method,
        "business_type": business.business_type,
        "business_type_label": business.business_type_label,
        "city": business.city,
        "state": business.state,
        "gst_number": business.gst_number,
        "created_at": business.created_at,
        "owner": _user_to_dict(owner) if owner else None,
        "users": [_user_to_dict(u) for u in users],
        "counts": counts,
        "recent_leads": [
            {
                "id": str(l.id),
                "title": getattr(l, "title", None),
                "created_at": l.created_at,
            }
            for l in recent_leads
        ],
        "recent_invoices": [
            {
                "id": str(i.id),
                "invoice_number": i.invoice_number,
                "status": i.status.value if hasattr(i.status, "value") else str(i.status),
                "total_amount": float(i.total_amount or 0),
                "created_at": i.created_at,
            }
            for i in recent_invoices
        ],
        "recent_payments": [
            {
                "id": str(p.id),
                "invoice_id": str(p.invoice_id),
                "amount": float(p.amount or 0),
                "method": p.payment_method.value if hasattr(p.payment_method, "value") else str(p.payment_method),
                "voided_at": p.voided_at,
                "created_at": p.created_at,
            }
            for p in recent_payments
        ],
    }


def _user_to_dict(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "is_active": user.is_active,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
    }


def _count_business_data(session: Session, business_id: uuid.UUID) -> dict[str, int]:
    """
    Counts of every business-scoped table. Used both for the detail view
    and the audit-log before-snapshot on delete.
    """
    sql = text(
        """
        SELECT
            (SELECT COUNT(*) FROM users          WHERE business_id = :biz) AS users,
            (SELECT COUNT(*) FROM customers      WHERE business_id = :biz) AS customers,
            (SELECT COUNT(*) FROM leads          WHERE business_id = :biz) AS leads,
            (SELECT COUNT(*) FROM lead_followups WHERE lead_id IN (SELECT id FROM leads WHERE business_id = :biz)) AS lead_followups,
            (SELECT COUNT(*) FROM lead_activities WHERE lead_id IN (SELECT id FROM leads WHERE business_id = :biz)) AS lead_activities,
            (SELECT COUNT(*) FROM invoices       WHERE business_id = :biz) AS invoices,
            (SELECT COUNT(*) FROM invoice_items  WHERE invoice_id IN (SELECT id FROM invoices WHERE business_id = :biz)) AS invoice_items,
            (SELECT COUNT(*) FROM invoice_adjustments WHERE invoice_id IN (SELECT id FROM invoices WHERE business_id = :biz)) AS invoice_adjustments,
            (SELECT COUNT(*) FROM payments       WHERE business_id = :biz) AS payments,
            (SELECT COUNT(*) FROM invoice_templates WHERE business_id = :biz) AS invoice_templates,
            (SELECT COUNT(*) FROM catalog_items  WHERE business_id = :biz) AS catalog_items,
            (SELECT COUNT(*) FROM quotes         WHERE business_id = :biz) AS quotes,
            (SELECT COUNT(*) FROM bookings       WHERE business_id = :biz) AS bookings,
            (SELECT COUNT(*) FROM tasks          WHERE business_id = :biz) AS tasks,
            (SELECT COUNT(*) FROM meetings       WHERE business_id = :biz) AS meetings,
            (SELECT COUNT(*) FROM messages       WHERE business_id = :biz) AS messages,
            (SELECT COUNT(*) FROM attachments    WHERE business_id = :biz) AS attachments,
            (SELECT COUNT(*) FROM chat_threads   WHERE business_id = :biz) AS chat_threads,
            (SELECT COUNT(*) FROM chat_messages  WHERE thread_id IN (SELECT id FROM chat_threads WHERE business_id = :biz)) AS chat_messages,
            (SELECT COUNT(*) FROM whatsapp_accounts      WHERE business_id = :biz) AS whatsapp_accounts,
            (SELECT COUNT(*) FROM whatsapp_conversations WHERE business_id = :biz) AS whatsapp_conversations,
            (SELECT COUNT(*) FROM whatsapp_messages      WHERE business_id = :biz) AS whatsapp_messages
        """
    )
    row = session.execute(sql, {"biz": str(business_id)}).first()
    if row is None:
        return {}
    return {
        "users": int(row.users or 0),
        "customers": int(row.customers or 0),
        "leads": int(row.leads or 0),
        "lead_followups": int(row.lead_followups or 0),
        "lead_activities": int(row.lead_activities or 0),
        "invoices": int(row.invoices or 0),
        "invoice_items": int(row.invoice_items or 0),
        "invoice_adjustments": int(row.invoice_adjustments or 0),
        "payments": int(row.payments or 0),
        "invoice_templates": int(row.invoice_templates or 0),
        "catalog_items": int(row.catalog_items or 0),
        "quotes": int(row.quotes or 0),
        "bookings": int(row.bookings or 0),
        "tasks": int(row.tasks or 0),
        "meetings": int(row.meetings or 0),
        "messages": int(row.messages or 0),
        "attachments": int(row.attachments or 0),
        "chat_threads": int(row.chat_threads or 0),
        "chat_messages": int(row.chat_messages or 0),
        "whatsapp_accounts": int(row.whatsapp_accounts or 0),
        "whatsapp_conversations": int(row.whatsapp_conversations or 0),
        "whatsapp_messages": int(row.whatsapp_messages or 0),
    }


# ───────────────────────────── delete ─────────────────────────────


def delete_business_cascade(
    session: Session,
    *,
    business_id: uuid.UUID,
    admin_email: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict[str, Any]:
    """
    Hard-delete a business and every child record. Returns the
    before-snapshot that was written to the audit log.

    Single transaction: either everything is gone, or nothing changes.
    """
    business = session.get(Business, business_id)
    if business is None:
        raise ValueError(f"Business {business_id} not found")

    # Snapshot before deleting — written to audit log so we can answer
    # "what did I delete on Tuesday?" later.
    snapshot: dict[str, Any] = {
        "business": {
            "id": str(business.id),
            "name": business.name,
            "phone": business.phone,
            "email": business.email,
            "created_at": business.created_at.isoformat() if business.created_at else None,
        },
        "counts": _count_business_data(session, business_id),
    }

    # Write audit log BEFORE the delete commits, so even if the cascade
    # fails halfway through, we have a record of the attempt. The audit
    # write commits separately.
    write_audit(
        session,
        admin_email=admin_email,
        action="business.delete",
        target_type="business",
        target_id=business_id,
        before_snapshot=_jsonable(snapshot),
        ip_address=ip_address,
        user_agent=user_agent,
    )

    # Run the cascade in one transaction.
    try:
        for stmt in _CASCADE_STATEMENTS:
            session.execute(text(stmt), {"biz": str(business_id)})
        session.commit()
    except Exception:
        session.rollback()
        raise

    return snapshot


def _jsonable(value: Any) -> Any:
    """
    Convert datetimes / Decimals / UUIDs to JSON-safe primitives for
    storage in the audit log's jsonb column.
    """
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value
