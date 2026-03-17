from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.whatsapp_conversation import (
    FindOrCreateConversationByLeadRequest,
    WhatsAppConversationLink,
    WhatsAppConversationRead,
)
from app.services.whatsapp_conversation_service import (
    find_or_create_by_lead,
    get_conversation,
    link_conversation,
    list_conversations,
    to_read,
)

router = APIRouter()


@router.get("/whatsapp/conversations", response_model=List[WhatsAppConversationRead])
def list_whatsapp_conversations(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    lead_id: Optional[UUID] = None,
    customer_id: Optional[UUID] = None,
) -> List[WhatsAppConversationRead]:
    """List conversations for current business; optional filters by lead_id, customer_id."""
    convs = list_conversations(
        session=session,
        current_user=current_user,
        lead_id=lead_id,
        customer_id=customer_id,
    )
    return [to_read(c) for c in convs]


@router.get("/whatsapp/conversations/{conversation_id}", response_model=WhatsAppConversationRead)
def get_whatsapp_conversation(
    conversation_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> WhatsAppConversationRead:
    """Get a single conversation by id."""
    conv = get_conversation(
        session=session,
        current_user=current_user,
        conversation_id=conversation_id,
    )
    return to_read(conv)


@router.post("/whatsapp/conversations/{conversation_id}/link", response_model=WhatsAppConversationRead)
def link_whatsapp_conversation(
    conversation_id: UUID,
    payload: WhatsAppConversationLink,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> WhatsAppConversationRead:
    """Link conversation to a lead and/or customer."""
    conv = link_conversation(
        session=session,
        current_user=current_user,
        conversation_id=conversation_id,
        data=payload,
    )
    return to_read(conv)


@router.post(
    "/whatsapp/conversations/find-or-create-by-lead",
    response_model=WhatsAppConversationRead,
)
def find_or_create_whatsapp_conversation_by_lead(
    payload: FindOrCreateConversationByLeadRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> WhatsAppConversationRead:
    conv = find_or_create_by_lead(
        session=session,
        business_id=current_user.business_id,
        lead_id=payload.lead_id,
    )
    return to_read(conv)
