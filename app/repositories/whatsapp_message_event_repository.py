from sqlmodel import Session

from app.models.whatsapp_message_event import WhatsAppMessageEvent


def create_whatsapp_message_event(
    session: Session,
    event: WhatsAppMessageEvent,
) -> WhatsAppMessageEvent:
    session.add(event)
    session.commit()
    session.refresh(event)
    return event
