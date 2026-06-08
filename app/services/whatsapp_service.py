"""WhatsApp Cloud API webhook service — verification + inbound ingest.

Handles Meta's HMAC-SHA256 signature check and the entry/changes/value loop
that drops messages and status updates onto the route. Outbound/send is a
later task; this module is read-side only for now.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import Session

from app.core.config import settings
from app.core.database import engine
from app.core.phone import normalize_phone_value
from app.models.common import utcnow
from app.models.wa_message import WaMessage
from app.repositories.lead_repository import get_lead_by_phone_normalized
from app.repositories.whatsapp_repo import (
    find_business_by_phone_id,
    insert_message,
    message_exists,
    update_message_status,
)
from app.services.whatsapp_client_factory import (
    WhatsAppNotConnected,
    get_client,
)
from uuid import UUID

logger = logging.getLogger(__name__)

_TEXT_TYPE = "text"
_MEDIA_TYPES = {"image", "document", "audio", "video", "sticker"}


def verify_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """Validate Meta's X-Hub-Signature-256 header. Meta sends the header as
    'sha256=<hex>' computed over the raw request bytes with WA_APP_SECRET as
    the key. We compare with hmac.compare_digest to avoid timing leaks."""
    secret = settings.WA_APP_SECRET
    if not secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    received = signature_header.split("=", 1)[1].strip()
    expected = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(received, expected)


def handle_event(payload: dict[str, Any]) -> None:
    """Top-level webhook payload dispatcher. Opens its own DB session because
    it runs detached from the request scope (the route fires this via
    asyncio.create_task so the 200 ACK comes back fast)."""
    try:
        with Session(engine) as session:
            _process_payload(session, payload)
    except Exception:
        # Swallow + log: webhook processing must never crash a background task
        # in a way that gets reported back to Meta as a delivery failure.
        logger.exception("WhatsApp webhook handle_event failed")


def _process_payload(session: Session, payload: dict[str, Any]) -> None:
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue

            metadata = value.get("metadata") or {}
            phone_number_id = metadata.get("phone_number_id")
            if not phone_number_id:
                logger.warning("WA webhook: missing phone_number_id in metadata")
                continue

            credential = find_business_by_phone_id(session, str(phone_number_id))
            if credential is None:
                logger.warning(
                    "WA webhook: no business onboarded for phone_number_id=%s",
                    phone_number_id,
                )
                continue

            display_phone = metadata.get("display_phone_number") or credential.display_phone

            for msg in value.get("messages") or []:
                if isinstance(msg, dict):
                    _ingest_inbound_message(
                        session=session,
                        business_id=credential.business_id,
                        owner_display_phone=str(display_phone or ""),
                        msg=msg,
                    )

            for status in value.get("statuses") or []:
                if isinstance(status, dict):
                    _apply_status_update(session, status)


def _ingest_inbound_message(
    *,
    session: Session,
    business_id,
    owner_display_phone: str,
    msg: dict[str, Any],
) -> None:
    wamid = msg.get("id")
    if not wamid:
        logger.warning("WA webhook: inbound message missing id, skipping")
        return

    if message_exists(session, str(wamid)):
        # Meta retries on non-2xx, and a single delivery can repeat — bail.
        return

    from_phone = str(msg.get("from") or "").strip()
    if not from_phone:
        logger.warning("WA webhook: inbound %s missing 'from', skipping", wamid)
        return

    # Coexistence echo: when the business owner sends a message from their own
    # phone (via the WhatsApp consumer app, same number as the Cloud API),
    # Meta delivers it as an inbound webhook with `from` == display_phone.
    # We persist it but flag it so it doesn't get treated as a customer reply.
    is_owner_echo = bool(
        owner_display_phone
        and normalize_phone_value(from_phone) == normalize_phone_value(owner_display_phone)
    )

    contact_phone_normalized = normalize_phone_value(from_phone)
    lead_id = None
    if not is_owner_echo:
        lead = get_lead_by_phone_normalized(
            session, business_id, contact_phone_normalized
        )
        if lead is not None:
            lead_id = lead.id

    msg_type = (msg.get("type") or _TEXT_TYPE).lower()
    body: Optional[str] = None
    media_url: Optional[str] = None
    if msg_type == _TEXT_TYPE:
        text_obj = msg.get("text") or {}
        if isinstance(text_obj, dict):
            body = text_obj.get("body")
    elif msg_type in _MEDIA_TYPES:
        # TODO: media download is a later task — we need to call the Meta
        # /media endpoint with the access token to resolve a real URL and
        # then mirror the bytes to R2. For now record the type only.
        media_url = None

    wa_timestamp = _parse_wa_timestamp(msg.get("timestamp"))

    record = WaMessage(
        business_id=business_id,
        wamid=str(wamid),
        direction="inbound",
        lead_id=lead_id,
        contact_phone=contact_phone_normalized or from_phone,
        msg_type=msg_type,
        body=body,
        media_url=media_url,
        template_name=None,
        status="received",
        error_detail=None,
        is_owner_echo=is_owner_echo,
        wa_timestamp=wa_timestamp,
    )
    insert_message(session, record)


def _parse_wa_timestamp(value: Any) -> datetime:
    if value is None:
        return utcnow()
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError):
        return utcnow()


def _apply_status_update(session: Session, status: dict[str, Any]) -> None:
    """Webhook 'statuses' callback: advance our row's status monotonically.
    The repo enforces no-regress (delivered < read won't downgrade)."""
    wamid = status.get("id")
    status_str = (status.get("status") or "").lower()
    if not wamid or status_str not in {"sent", "delivered", "read", "failed"}:
        return

    error_detail: Optional[str] = None
    if status_str == "failed":
        errors = status.get("errors") or []
        if errors and isinstance(errors[0], dict):
            err = errors[0]
            error_detail = err.get("message") or err.get("title") or str(err)

    updated = update_message_status(
        session, str(wamid), status_str, error_detail=error_detail
    )
    if updated is None:
        logger.info("WA status: no row for wamid=%s (status=%s)", wamid, status_str)


# --- Outbound -----------------------------------------------------------------


async def send_text_message(
    business_id: UUID,
    to_phone: str,
    body: str,
    lead_id: Optional[UUID] = None,
) -> WaMessage:
    """Send a free-form text via pywa_async and persist the row.

    On Meta failure: persist a row with status='failed' + error_detail, then
    re-raise so the caller knows the send didn't happen."""
    if not body or not body.strip():
        raise ValueError("body must be a non-empty string")

    normalized_to = normalize_phone_value(to_phone)
    if not normalized_to:
        raise ValueError("to_phone is empty after normalization")

    client = get_client(business_id)  # raises WhatsAppNotConnected if not onboarded

    try:
        sent = await client.send_message(to=normalized_to.lstrip("+"), text=body)
    except Exception as exc:
        # Persist the failed attempt so the operator can see it in wa_messages.
        # No wamid is known on outbound failure, so synthesize a placeholder
        # — the UNIQUE constraint means we must not collide with real wamids.
        synthetic = f"local-failed-{utcnow().strftime('%Y%m%dT%H%M%S%f')}"
        failed_row = WaMessage(
            business_id=business_id,
            wamid=synthetic,
            direction="outbound",
            lead_id=lead_id,
            contact_phone=normalized_to,
            msg_type="text",
            body=body,
            media_url=None,
            template_name=None,
            status="failed",
            error_detail=str(exc)[:500],
            is_owner_echo=False,
            wa_timestamp=utcnow(),
        )
        with Session(engine) as s:
            insert_message(s, failed_row)
        logger.warning("WA send failed for business=%s to=%s", business_id, normalized_to)
        raise

    # pywa_async returns the wamid string (or an object with .id depending on
    # version). Normalize both.
    wamid = getattr(sent, "id", None) or str(sent)

    sent_row = WaMessage(
        business_id=business_id,
        wamid=wamid,
        direction="outbound",
        lead_id=lead_id,
        contact_phone=normalized_to,
        msg_type="text",
        body=body,
        media_url=None,
        template_name=None,
        status="sent",
        error_detail=None,
        is_owner_echo=False,
        wa_timestamp=utcnow(),
    )
    with Session(engine) as s:
        insert_message(s, sent_row)
    return sent_row


__all__ = [
    "verify_signature",
    "handle_event",
    "send_text_message",
    "WhatsAppNotConnected",
]
