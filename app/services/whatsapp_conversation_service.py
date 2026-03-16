from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.common import utcnow
from app.models.lead import Lead
from app.models.user import User
from app.models.whatsapp_conversation import (
    WhatsAppConversation,
    WhatsAppConversationLink,
    WhatsAppConversationRead,
)
from app.repositories.customer_repository import get_customers_by_phone
from app.repositories.whatsapp_account_repository import get_whatsapp_account_by_id
from app.repositories.whatsapp_conversation_repository import (
    create_whatsapp_conversation,
    get_whatsapp_conversation_by_id,
    get_whatsapp_conversation_by_phone,
    list_whatsapp_conversations_for_business,
    update_whatsapp_conversation,
)


def get_conversation(
    session: Session,
    current_user: User,
    conversation_id: UUID,
) -> WhatsAppConversation:
    """Get a conversation by id; must belong to current business."""
    conv = get_whatsapp_conversation_by_id(
        session, business_id=current_user.business_id, conversation_id=conversation_id
    )
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return conv


def list_conversations(
    session: Session,
    current_user: User,
    lead_id: Optional[UUID] = None,
    customer_id: Optional[UUID] = None,
) -> List[WhatsAppConversation]:
    """List conversations for current business with optional filters."""
    return list_whatsapp_conversations_for_business(
        session,
        business_id=current_user.business_id,
        lead_id=lead_id,
        customer_id=customer_id,
    )


def link_conversation(
    session: Session,
    current_user: User,
    conversation_id: UUID,
    data: WhatsAppConversationLink,
) -> WhatsAppConversation:
    """Link a conversation to a lead and/or customer."""
    conv = get_conversation(session, current_user, conversation_id)
    if data.lead_id is not None:
        conv.lead_id = data.lead_id
    if data.customer_id is not None:
        conv.customer_id = data.customer_id
    return update_whatsapp_conversation(session, conv)


def find_or_create_conversation(
    session: Session,
    business_id: UUID,
    whatsapp_account_id: UUID,
    phone_number: str,
    contact_name: Optional[str] = None,
) -> WhatsAppConversation:
    """
    Find or create a conversation for the given account and phone.
    Optionally auto-link to customer/lead when exactly one match by phone.
    """
    account = get_whatsapp_account_by_id(session, business_id, whatsapp_account_id)
    if account is None:
        raise ValueError("WhatsApp account not found")

    conv = get_whatsapp_conversation_by_phone(
        session, business_id, whatsapp_account_id, phone_number
    )
    if conv is not None:
        if contact_name and conv.contact_name != contact_name:
            conv.contact_name = contact_name
            update_whatsapp_conversation(session, conv)
        return conv

    # Auto-linking: try to attach customer_id / lead_id when exactly one match.
    customer_id: Optional[UUID] = None
    lead_id: Optional[UUID] = None
    try:
        customers = get_customers_by_phone(session, business_id, phone_number)
        if len(customers) == 1:
            customer_id = customers[0].id
            # If that customer has exactly one lead, attach lead_id.
            stmt = select(Lead).where(
                Lead.business_id == business_id,
                Lead.customer_id == customer_id,
            )
            leads = list(session.exec(stmt).all())
            if len(leads) == 1:
                lead_id = leads[0].id
    except Exception:
        pass  # Keep placeholders; do not block conversation creation

    now = utcnow()
    conv = WhatsAppConversation(
        business_id=business_id,
        whatsapp_account_id=whatsapp_account_id,
        customer_id=customer_id,
        lead_id=lead_id,
        phone_number=phone_number,
        contact_name=contact_name,
        last_message_at=now,
        opened_at=now,
    )
    return create_whatsapp_conversation(session, conv)


def to_read(conv: WhatsAppConversation) -> WhatsAppConversationRead:
    """Map conversation model to read schema."""
    return WhatsAppConversationRead(
        id=conv.id,
        business_id=conv.business_id,
        whatsapp_account_id=conv.whatsapp_account_id,
        customer_id=conv.customer_id,
        lead_id=conv.lead_id,
        phone_number=conv.phone_number,
        contact_name=conv.contact_name,
        last_message_at=conv.last_message_at,
        last_incoming_at=conv.last_incoming_at,
        last_outgoing_at=conv.last_outgoing_at,
        opened_at=conv.opened_at,
        closed_at=conv.closed_at,
        is_blocked=conv.is_blocked,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )
