from typing import List, Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.whatsapp_conversation import WhatsAppConversation


def create_whatsapp_conversation(
    session: Session,
    conversation: WhatsAppConversation,
) -> WhatsAppConversation:
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def create_conversation(
    session: Session,
    conversation: WhatsAppConversation,
) -> WhatsAppConversation:
    return create_whatsapp_conversation(session, conversation)


def get_whatsapp_conversation_by_id(
    session: Session,
    business_id: UUID,
    conversation_id: UUID,
) -> Optional[WhatsAppConversation]:
    statement = select(WhatsAppConversation).where(
        WhatsAppConversation.id == conversation_id,
        WhatsAppConversation.business_id == business_id,
    )
    return session.exec(statement).first()


def get_whatsapp_conversation_by_phone(
    session: Session,
    business_id: UUID,
    whatsapp_account_id: UUID,
    phone_number: str,
) -> Optional[WhatsAppConversation]:
    statement = select(WhatsAppConversation).where(
        WhatsAppConversation.business_id == business_id,
        WhatsAppConversation.whatsapp_account_id == whatsapp_account_id,
        WhatsAppConversation.phone_number == phone_number,
    )
    return session.exec(statement).first()


def get_by_phone(
    session: Session,
    business_id: UUID,
    phone_number: str,
    whatsapp_account_id: UUID | None = None,
) -> Optional[WhatsAppConversation]:
    statement = select(WhatsAppConversation).where(
        WhatsAppConversation.business_id == business_id,
        WhatsAppConversation.phone_number == phone_number,
    )
    if whatsapp_account_id is not None:
        statement = statement.where(
            WhatsAppConversation.whatsapp_account_id == whatsapp_account_id,
        )
    return session.exec(statement).first()


def list_whatsapp_conversations_for_business(
    session: Session,
    business_id: UUID,
    lead_id: Optional[UUID] = None,
    customer_id: Optional[UUID] = None,
) -> List[WhatsAppConversation]:
    statement = select(WhatsAppConversation).where(
        WhatsAppConversation.business_id == business_id,
    )
    if lead_id is not None:
        statement = statement.where(WhatsAppConversation.lead_id == lead_id)
    if customer_id is not None:
        statement = statement.where(WhatsAppConversation.customer_id == customer_id)
    statement = statement.order_by(WhatsAppConversation.last_message_at.desc())
    return list(session.exec(statement).all())


def update_whatsapp_conversation(
    session: Session,
    conversation: WhatsAppConversation,
) -> WhatsAppConversation:
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def update_conversation(
    session: Session,
    conversation: WhatsAppConversation,
) -> WhatsAppConversation:
    return update_whatsapp_conversation(session, conversation)
