from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.whatsapp_message import (
    WhatsAppMessage,
    WhatsAppMessageRead,
    WhatsAppMessageSendDocument,
    WhatsAppMessageSendText,
)
from app.services.whatsapp_message_service import (
    list_messages,
    send_document_message,
    send_text_message,
)

router = APIRouter()


def _message_to_read(m: WhatsAppMessage) -> WhatsAppMessageRead:
    return WhatsAppMessageRead(
        id=m.id,
        business_id=m.business_id,
        conversation_id=m.conversation_id,
        whatsapp_account_id=m.whatsapp_account_id,
        customer_id=m.customer_id,
        lead_id=m.lead_id,
        whatsapp_message_id=m.whatsapp_message_id,
        direction=m.direction,
        message_type=m.message_type,
        text_body=m.text_body,
        media_url=m.media_url,
        mime_type=m.mime_type,
        file_name=m.file_name,
        caption=m.caption,
        status=m.status,
        sent_at=m.sent_at,
        delivered_at=m.delivered_at,
        read_at=m.read_at,
        failed_at=m.failed_at,
        error_message=m.error_message,
        raw_payload=m.raw_payload,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


@router.get(
    "/whatsapp/conversations/{conversation_id}/messages",
    response_model=List[WhatsAppMessageRead],
)
def list_whatsapp_messages(
    conversation_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    limit: int = 50,
    offset: int = 0,
) -> List[WhatsAppMessageRead]:
    """List messages for a conversation with pagination."""
    messages = list_messages(
        session=session,
        current_user=current_user,
        conversation_id=conversation_id,
        limit=limit,
        offset=offset,
    )
    return [_message_to_read(m) for m in messages]


@router.post("/whatsapp/messages/text", response_model=WhatsAppMessageRead)
def send_text(
    payload: WhatsAppMessageSendText,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> WhatsAppMessageRead:
    """Send a text message in a conversation."""
    message = send_text_message(
        session=session,
        current_user=current_user,
        data=payload,
    )
    return _message_to_read(message)


@router.post("/whatsapp/messages/document", response_model=WhatsAppMessageRead)
def send_document(
    payload: WhatsAppMessageSendDocument,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> WhatsAppMessageRead:
    """Send a document message in a conversation."""
    message = send_document_message(
        session=session,
        current_user=current_user,
        data=payload,
    )
    return _message_to_read(message)
