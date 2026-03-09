from typing import List
from uuid import UUID

from sqlmodel import Session, select

from app.models.message import Message


def create_message(session: Session, message: Message) -> Message:
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


def list_messages_for_business(
    session: Session,
    business_id: UUID,
) -> List[Message]:
    statement = select(Message).where(Message.business_id == business_id)
    return list(session.exec(statement).all())

