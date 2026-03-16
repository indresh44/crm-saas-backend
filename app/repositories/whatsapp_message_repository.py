from typing import List, Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.whatsapp_message import WhatsAppMessage


def create_whatsapp_message(session: Session, message: WhatsAppMessage) -> WhatsAppMessage:
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


def get_whatsapp_message_by_id(
    session: Session,
    business_id: UUID,
    message_id: UUID,
) -> Optional[WhatsAppMessage]:
    statement = select(WhatsAppMessage).where(
        WhatsAppMessage.id == message_id,
        WhatsAppMessage.business_id == business_id,
    )
    return session.exec(statement).first()


def get_whatsapp_message_by_whatsapp_message_id(
    session: Session,
    whatsapp_message_id: str,
) -> Optional[WhatsAppMessage]:
    statement = select(WhatsAppMessage).where(
        WhatsAppMessage.whatsapp_message_id == whatsapp_message_id,
    )
    return session.exec(statement).first()


def list_whatsapp_messages_for_conversation(
    session: Session,
    business_id: UUID,
    conversation_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> List[WhatsAppMessage]:
    statement = (
        select(WhatsAppMessage)
        .where(
            WhatsAppMessage.business_id == business_id,
            WhatsAppMessage.conversation_id == conversation_id,
        )
        .order_by(WhatsAppMessage.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.exec(statement).all())


def update_whatsapp_message(session: Session, message: WhatsAppMessage) -> WhatsAppMessage:
    session.add(message)
    session.commit()
    session.refresh(message)
    return message
