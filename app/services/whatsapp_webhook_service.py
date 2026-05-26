"""
Process Meta WhatsApp Cloud API webhook payloads: incoming messages and status updates.
Uses defensive parsing; payload structure may vary.
"""

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.models.common import utcnow
from app.models.enums import (
    WhatsAppMessageDirection,
    WhatsAppMessageEventType,
    WhatsAppMessageStatus,
    WhatsAppMessageType,
)
from app.models.whatsapp_message import WhatsAppMessage
from app.models.whatsapp_message_event import WhatsAppMessageEvent
from app.models.lead import LeadActivity
from app.core.actor_context import set_actor_context
from app.models.enums import ActorType, LeadActivityType
from app.repositories.lead_repository import create_lead_activity
from app.repositories.user_repository import get_one_user_for_business
from app.repositories.whatsapp_message_event_repository import create_whatsapp_message_event
from app.repositories.whatsapp_message_repository import (
    create_whatsapp_message,
    get_whatsapp_message_by_whatsapp_message_id,
    update_whatsapp_message,
)
from app.repositories.whatsapp_account_repository import get_whatsapp_account_by_phone_number_id
from app.services.whatsapp_conversation_service import find_or_create_conversation
from datetime import datetime
import json

logger = logging.getLogger(__name__)


def process_webhook_payload(session: Session, payload: dict[str, Any]) -> None:
    """
    Process incoming webhook: route to message handling or status handling.
    Payload: { "object": "whatsapp_business_account", "entry": [ { "id", "changes": [ { "value": {...}, "field": "messages" } ] } ] }
    """
    # Log the incoming payload for debugging/audit purposes
    try:
        with open("/root/workspace/crm-saas-backend/log.txt", "a", encoding="utf-8") as log_file:
            log_file.write(
                f"{datetime.utcnow().isoformat()}Z - Incoming webhook payload:\n"
                f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
            )
    except Exception as e:
        # Fallback: ignore logging errors to avoid breaking webhook processing
        pass
    entries = payload.get("entry") or []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            field = change.get("field")
            if not value or not isinstance(value, dict):
                continue
            if field == "messages":
                _process_messages_value(session, value)
            if "statuses" in value:
                _process_statuses_value(session, value)


def _process_messages_value(session: Session, value: dict[str, Any]) -> None:
    """Handle incoming messages: create conversation, message, event; update timestamps."""
    metadata = value.get("metadata") or {}
    phone_number_id = metadata.get("phone_number_id")
    if not phone_number_id:
        phone_number_id = str(metadata.get("phone_number_id", ""))
    if not phone_number_id:
        logger.warning("No phone_number_id in webhook metadata")
        return

    logger.info(f"Processing incoming messages for phone_number_id: {phone_number_id}")
    
    account = get_whatsapp_account_by_phone_number_id(session, str(phone_number_id))
    if not account:
        logger.error(f"WhatsApp account not found for phone_number_id: {phone_number_id}")
        return
    
    if not account.is_active:
        logger.warning(f"WhatsApp account is inactive for phone_number_id: {phone_number_id}")
        return

    business_id = account.business_id
    messages_list = value.get("messages") or []
    contacts_list = value.get("contacts") or []

    logger.info(f"Found {len(messages_list)} messages to process for account {account.id}")

    for msg in messages_list:
        if not isinstance(msg, dict):
            logger.warning("Message is not a dict, skipping")
            continue
        msg_id = msg.get("id")
        from_phone = msg.get("from")
        if not from_phone:
            logger.warning("Message has no 'from' field, skipping")
            continue
        from_phone = str(from_phone).replace(" ", "").strip()
        timestamp = msg.get("timestamp")
        msg_type = (msg.get("type") or "text").lower()
        contact_name = None
        if contacts_list and isinstance(contacts_list[0], dict):
            profile = contacts_list[0].get("profile") or {}
            contact_name = profile.get("name") if isinstance(profile, dict) else None

        logger.info(f"Processing message {msg_id} from {from_phone}, type: {msg_type}")
        
        try:
            conv = find_or_create_conversation(
                session,
                business_id=business_id,
                whatsapp_account_id=account.id,
                phone_number=from_phone,
                contact_name=contact_name,
            )
        except Exception as e:
            logger.error(f"Failed to find or create conversation for {from_phone}: {str(e)}", exc_info=True)
            continue

        text_body = None
        if msg_type == "text":
            text_obj = msg.get("text") or {}
            text_body = text_obj.get("body") if isinstance(text_obj, dict) else None
        media_url = None
        caption = None
        file_name = None
        mime_type = None
        if msg_type in ("image", "document", "audio", "video"):
            media_key = msg_type
            media_obj = msg.get(media_key) or msg.get("document") or msg.get("image") or {}
            if isinstance(media_obj, dict):
                media_url = media_obj.get("url") or media_obj.get("link")
                caption = media_obj.get("caption")
                file_name = (media_obj.get("filename") or media_obj.get("id")) if msg_type == "document" else None
                mime_type = media_obj.get("mime_type")

        wa_message_type = _map_message_type(msg_type)
        now = utcnow()
        
        # Convert timestamp from string to int if present
        sent_at = now
        if timestamp:
            try:
                sent_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse timestamp {timestamp}: {str(e)}")
                sent_at = now
        
        incoming = WhatsAppMessage(
            business_id=business_id,
            conversation_id=conv.id,
            whatsapp_account_id=account.id,
            customer_id=conv.customer_id,
            lead_id=conv.lead_id,
            whatsapp_message_id=msg_id,
            direction=WhatsAppMessageDirection.INCOMING,
            message_type=wa_message_type,
            text_body=text_body,
            media_url=media_url,
            caption=caption,
            file_name=file_name,
            mime_type=mime_type,
            status=WhatsAppMessageStatus.DELIVERED,
            sent_at=sent_at,
            raw_payload=msg,
        )
        try:
            created_msg = create_whatsapp_message(session, incoming)
            logger.info(f"Successfully created message {created_msg.id} for conversation {conv.id}")
        except Exception as e:
            logger.error(f"Failed to create message: {str(e)}", exc_info=True)
            continue

        event = WhatsAppMessageEvent(
            business_id=business_id,
            whatsapp_message_id_ref=incoming.id,
            event_type=WhatsAppMessageEventType.WEBHOOK_RECEIVED,
            payload=msg,
            event_at=now,
        )
        try:
            create_whatsapp_message_event(session, event)
        except Exception as e:
            logger.error(f"Failed to create message event: {str(e)}", exc_info=True)

        try:
            conv.last_message_at = now
            conv.last_incoming_at = now
            from app.repositories.whatsapp_conversation_repository import (
                update_whatsapp_conversation,
            )
            update_whatsapp_conversation(session, conv)
            logger.info(f"Updated conversation {conv.id} timestamps")
        except Exception as e:
            logger.error(f"Failed to update conversation: {str(e)}", exc_info=True)

        try:
            if conv.lead_id:
                _create_incoming_lead_activity(session, business_id, conv.lead_id, text_body or "(media)")
        except Exception as e:
            logger.error(f"Failed to create lead activity: {str(e)}", exc_info=True)


def _create_incoming_lead_activity(
    session: Session,
    business_id: UUID,
    lead_id: UUID,
    content: str,
) -> None:
    """Create LeadActivity for incoming message when conversation is linked
    to a lead. The actor is SYSTEM (webhook, no real user); created_by is
    NULL — this was previously misattributed to the first user in the
    business (see TODO removed below). 0042 made created_by nullable
    precisely so this can be honest now."""
    desc = f"WhatsApp incoming: {content[:200]}{'...' if len(content) > 200 else ''}"
    activity = LeadActivity(
        lead_id=lead_id,
        type=LeadActivityType.WHATSAPP,
        description=desc,
        created_by=None,           # webhook — no real user
        # actor_type stamped by the chokepoint from the SYSTEM context bound
        # in the webhook router. Payload stays NULL for WHATSAPP — the
        # message content already lives in description.
    )
    with set_actor_context(ActorType.SYSTEM):
        create_lead_activity(session, activity)


def _map_message_type(msg_type: str) -> WhatsAppMessageType:
    m = {
        "text": WhatsAppMessageType.TEXT,
        "image": WhatsAppMessageType.IMAGE,
        "document": WhatsAppMessageType.DOCUMENT,
        "audio": WhatsAppMessageType.AUDIO,
        "video": WhatsAppMessageType.VIDEO,
        "interactive": WhatsAppMessageType.INTERACTIVE,
        "template": WhatsAppMessageType.TEMPLATE,
    }
    return m.get(msg_type, WhatsAppMessageType.UNKNOWN)


def _process_statuses_value(session: Session, value: dict[str, Any]) -> None:
    """Handle status updates: find message by whatsapp_message_id, update status and timestamps."""
    statuses = value.get("statuses") or []
    for st in statuses:
        if not isinstance(st, dict):
            continue
        wam_id = st.get("id")
        status_str = (st.get("status") or "").lower()
        timestamp = st.get("timestamp")
        if not wam_id:
            continue

        message = get_whatsapp_message_by_whatsapp_message_id(session, str(wam_id))
        if not message:
            continue

        # Convert timestamp from string to int if present
        now = utcnow()
        if timestamp:
            try:
                now = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse status timestamp {timestamp}: {str(e)}")
                now = utcnow()
        if status_str == "sent":
            message.status = WhatsAppMessageStatus.SENT
            message.sent_at = now
        elif status_str == "delivered":
            message.status = WhatsAppMessageStatus.DELIVERED
            message.delivered_at = now
        elif status_str == "read":
            message.status = WhatsAppMessageStatus.READ
            message.read_at = now
        elif status_str == "failed":
            message.status = WhatsAppMessageStatus.FAILED
            message.failed_at = now
            errors = st.get("errors") or []
            if errors and isinstance(errors[0], dict):
                message.error_message = errors[0].get("message") or errors[0].get("title")
        else:
            continue

        update_whatsapp_message(session, message)
        event_type = {
            "sent": WhatsAppMessageEventType.SENT,
            "delivered": WhatsAppMessageEventType.DELIVERED,
            "read": WhatsAppMessageEventType.READ,
            "failed": WhatsAppMessageEventType.FAILED,
        }.get(status_str)
        if event_type:
            ev = WhatsAppMessageEvent(
                business_id=message.business_id,
                whatsapp_message_id_ref=message.id,
                event_type=event_type,
                payload=st,
                event_at=now,
            )
            create_whatsapp_message_event(session, ev)
