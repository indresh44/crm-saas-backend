from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from app.models.common import utcnow
from app.models.enums import LeadActivityType
from app.models.lead import LeadActivity
from app.models.user import User
from app.models.whatsapp_message import (
    WhatsAppMessage,
    WhatsAppMessageSendDocument,
    WhatsAppMessageSendText,
)
from app.models.enums import (
    WhatsAppMessageDirection,
    WhatsAppMessageStatus,
    WhatsAppMessageType,
)
from app.repositories.lead_repository import create_lead_activity
from app.repositories.whatsapp_account_repository import get_whatsapp_account_by_id
from app.repositories.whatsapp_conversation_repository import (
    get_whatsapp_conversation_by_id,
    update_whatsapp_conversation,
)
from app.repositories.whatsapp_message_repository import (
    create_whatsapp_message,
    get_whatsapp_message_by_id,
    list_whatsapp_messages_for_conversation,
    update_whatsapp_message,
)
from app.repositories.whatsapp_message_event_repository import create_whatsapp_message_event
from app.services.whatsapp_client_service import send_document, send_text


def send_text_message(
    session: Session,
    current_user: User,
    data: WhatsAppMessageSendText,
) -> WhatsAppMessage:
    """Send a text message: create local PENDING row, call API, update with message id and status."""
    conv = get_whatsapp_conversation_by_id(
        session, business_id=current_user.business_id, conversation_id=data.conversation_id
    )
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    account = get_whatsapp_account_by_id(
        session, current_user.business_id, conv.whatsapp_account_id
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WhatsApp account not found",
        )

    now = utcnow()
    message = WhatsAppMessage(
        business_id=current_user.business_id,
        conversation_id=conv.id,
        whatsapp_account_id=account.id,
        customer_id=conv.customer_id,
        lead_id=conv.lead_id,
        direction=WhatsAppMessageDirection.OUTGOING,
        message_type=WhatsAppMessageType.TEXT,
        text_body=data.text,
        status=WhatsAppMessageStatus.PENDING,
    )
    create_whatsapp_message(session, message)

    try:
        result = send_text(account, conv.phone_number, data.text)
        wam_id = (result.get("messages") or [{}])[0].get("id") if result else None
        if wam_id:
            message.whatsapp_message_id = wam_id
            message.status = WhatsAppMessageStatus.SENT
            message.sent_at = utcnow()
    except Exception as e:
        message.status = WhatsAppMessageStatus.FAILED
        message.failed_at = utcnow()
        message.error_message = str(e)
    update_whatsapp_message(session, message)

    conv.last_message_at = now
    conv.last_outgoing_at = now
    update_whatsapp_conversation(session, conv)

    if conv.lead_id:
        _create_lead_activity(
            session,
            lead_id=conv.lead_id,
            created_by=current_user.id,
            description=f"WhatsApp text sent: {data.text[:100]}{'...' if len(data.text) > 100 else ''}",
        )

    return message


def send_document_message(
    session: Session,
    current_user: User,
    data: WhatsAppMessageSendDocument,
) -> WhatsAppMessage:
    """Send a document message; same flow as text."""
    conv = get_whatsapp_conversation_by_id(
        session, business_id=current_user.business_id, conversation_id=data.conversation_id
    )
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    account = get_whatsapp_account_by_id(
        session, current_user.business_id, conv.whatsapp_account_id
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WhatsApp account not found",
        )

    now = utcnow()
    message = WhatsAppMessage(
        business_id=current_user.business_id,
        conversation_id=conv.id,
        whatsapp_account_id=account.id,
        customer_id=conv.customer_id,
        lead_id=conv.lead_id,
        direction=WhatsAppMessageDirection.OUTGOING,
        message_type=WhatsAppMessageType.DOCUMENT,
        media_url=data.document_url,
        file_name=data.file_name,
        caption=data.caption,
        status=WhatsAppMessageStatus.PENDING,
    )
    create_whatsapp_message(session, message)

    try:
        result = send_document(
            account, conv.phone_number, data.document_url, data.file_name, data.caption
        )
        wam_id = (result.get("messages") or [{}])[0].get("id") if result else None
        if wam_id:
            message.whatsapp_message_id = wam_id
            message.status = WhatsAppMessageStatus.SENT
            message.sent_at = utcnow()
    except Exception as e:
        message.status = WhatsAppMessageStatus.FAILED
        message.failed_at = utcnow()
        message.error_message = str(e)
    update_whatsapp_message(session, message)

    conv.last_message_at = now
    conv.last_outgoing_at = now
    update_whatsapp_conversation(session, conv)

    if conv.lead_id:
        _create_lead_activity(
            session,
            lead_id=conv.lead_id,
            created_by=current_user.id,
            description=f"WhatsApp document sent: {data.file_name}",
        )

    return message


def list_messages(
    session: Session,
    current_user: User,
    conversation_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> List[WhatsAppMessage]:
    """List messages for a conversation (business-scoped)."""
    conv = get_whatsapp_conversation_by_id(
        session, business_id=current_user.business_id, conversation_id=conversation_id
    )
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return list_whatsapp_messages_for_conversation(
        session, current_user.business_id, conversation_id, limit=limit, offset=offset
    )


def _create_lead_activity(
    session: Session,
    lead_id: UUID,
    created_by: UUID,
    description: str,
) -> None:
    """Create a LeadActivity of type WHATSAPP for outgoing message."""
    activity = LeadActivity(
        lead_id=lead_id,
        type=LeadActivityType.WHATSAPP,
        description=description,
        created_by=created_by,
    )
    create_lead_activity(session, activity)
