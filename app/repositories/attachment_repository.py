from typing import List
from uuid import UUID

from sqlmodel import Session, select

from app.models.attachment import Attachment


def create_attachment(session: Session, attachment: Attachment) -> Attachment:
    session.add(attachment)
    session.commit()
    session.refresh(attachment)
    return attachment


def list_attachments_for_business(
    session: Session,
    business_id: UUID,
) -> List[Attachment]:
    statement = select(Attachment).where(Attachment.business_id == business_id)
    return list(session.exec(statement).all())

